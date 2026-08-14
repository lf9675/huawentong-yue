# -*- coding: utf-8 -*-
"""华文通·理 —— 引擎调用层（无 Streamlit 依赖）

转录：智谱 GLM-4V（视觉，便宜，跑一次存档）
评分：DeepSeek（判断力活，与「华文通·改」同一模型，校准口径一致）
诊断：DeepSeek，全班一次调用

2026-08-13 决策：从 Gemini + Claude 换成 智谱 + DeepSeek。
  原因：Claude 单价过高，无法支撑题海规模的大量批改。
  代价：两家均为中国托管，PDPA 层面会阻碍学校统一采购。
       将来若需换回非中国托管，只改本文件的 glm_vision() 与 deepseek_chat()，
       上层五个页面一行不用动。
两家都是 OpenAI 兼容接口，用标准库 urllib 直调，零第三方 SDK 依赖。
"""

import base64
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import prompts_yue as P

ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

TRANSCRIBE_MODEL = "glm-4v-flash"    # 备选 glm-4v-plus（更准更贵）
GRADE_MODEL = "deepseek-chat"
GRADE_TEMPERATURE = 0.2              # 与「华文通·改」一致
MAX_TOKENS = 8000
CONCURRENCY = 4                      # 智谱限流较紧，比 Gemini 保守
RETRY = 3


# ════════════════════════════════════════════════════════════
# 四层 JSON 防御 + 正则兜底：宁可降级输出，不给白屏
# ════════════════════════════════════════════════════════════
def _scan_objects(text):
    """扫出大括号配对完整的对象，截断的尾巴自动丢弃。"""
    out, depth, start, in_str, esc = [], 0, None, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                out.append(text[start:i + 1])
                start = None
    return out


def robust_json(text, list_key=None):
    """返回 (对象, 层级说明)。任何情况下不抛异常、不返回 None。"""
    trace = []
    try:
        return json.loads(text), "第1层 直接解析"
    except Exception as err:
        trace.append("L1:" + str(err)[:50])

    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    s, e = t.find("{"), t.rfind("}")
    if s != -1 and e > s:
        t = t[s:e + 1]
    try:
        return json.loads(t), "第2层 清洗后解析"
    except Exception as err:
        trace.append("L2:" + str(err)[:50])

    fixed = t.replace("\u201c", '"').replace("\u201d", '"')
    fixed = fixed.replace("\u2018", "'").replace("\u2019", "'").replace("\r", "")
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
    try:
        return json.loads(fixed), "第3层 引号修复"
    except Exception as err:
        trace.append("L3:" + str(err)[:50])

    rep = fixed
    if rep.count('"') % 2 == 1:
        rep += '"'
    rep += "]" * max(0, rep.count("[") - rep.count("]"))
    rep += "}" * max(0, rep.count("{") - rep.count("}"))
    try:
        return json.loads(rep), "第4层 截断恢复"
    except Exception as err:
        trace.append("L4a:" + str(err)[:50])

    if list_key:
        salvaged = []
        for blk in _scan_objects(fixed):
            if '"qid"' not in blk:
                continue
            try:
                salvaged.append(json.loads(blk))
            except Exception as err:
                trace.append("L4b:" + str(err)[:30])
        if salvaged:
            return ({list_key: salvaged},
                    "第4层 逐题恢复（捞回 " + str(len(salvaged)) + " 题）")

    items = []
    for m in re.finditer(r'"qid"\s*:\s*"?([^",}\s]+)', fixed):
        seg = fixed[m.end():m.end() + 800]
        sc = re.search(r'"score"\s*:\s*(\d+)', seg)
        items.append({"qid": m.group(1).strip(),
                      "score": int(sc.group(1)) if sc else 0,
                      "error_tags": [], "hit_points": [], "missed_points": [],
                      "why_wrong": "", "how_to_fix": "",
                      "teacher_note": "JSON 解析降级，需人工复核"})
    return ({list_key or "items": items},
            "第5层 正则兜底（需人工复核）｜" + " ".join(trace))


# ════════════════════════════════════════════════════════════
# 调用：两家都是 OpenAI 兼容接口，共用一个底座
# ════════════════════════════════════════════════════════════
def _chat(url, api_key, payload, timeout=180):
    """流式 POST，逐块拼回文本。限流和 5xx 自动退避重试。"""
    payload = dict(payload)
    payload["stream"] = True
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "Authorization": "Bearer " + api_key,
               "Accept": "text/event-stream"}

    last_err = ""
    for attempt in range(RETRY):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            out = []
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        obj = json.loads(chunk)
                    except ValueError:
                        continue
                    for ch in obj.get("choices") or []:
                        piece = (ch.get("delta") or {}).get("content")
                        if piece:
                            out.append(piece)
            text = "".join(out)
            if text.strip():
                return text
            last_err = "返回为空"
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:200]
            last_err = "HTTP " + str(e.code) + "：" + detail
            if e.code not in (429, 500, 502, 503, 504):
                raise RuntimeError(last_err)
        except Exception as e:
            last_err = str(e)[:200]
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("重试 " + str(RETRY) + " 次仍失败｜" + last_err)


def glm_vision(api_key, prompt, image_bytes, model=TRANSCRIBE_MODEL):
    """智谱 GLM-4V 读手写。"""
    return _chat(ZHIPU_URL, api_key, {
        "model": model,
        "temperature": 0.1,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode()}},
            {"type": "text", "text": prompt},
        ]}],
    }, timeout=120)


def deepseek_chat(api_key, system, user_text, model=GRADE_MODEL):
    """DeepSeek 评分／诊断。开 JSON 模式，从源头减少解析失败。"""
    return _chat(DEEPSEEK_URL, api_key, {
        "model": model,
        "temperature": GRADE_TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user_text}],
    })


def transcribe_one(zhipu_key, image_bytes):
    """单题作答图 → {transcript, ocr_flag}"""
    text = glm_vision(zhipu_key, P.TRANSCRIBE_PROMPT, image_bytes)
    obj, layer = robust_json(text)
    return {"transcript": (obj.get("transcript") or "").strip(),
            "ocr_flag": obj.get("ocr_flag") or "uncertain",
            "_layer": layer}


def transcribe_batch(zhipu_key, tasks, progress=None):
    """tasks: [{student_id, qid, image_bytes}] → [{student_id, qid, transcript, ocr_flag}]"""
    results, done = [], 0
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(transcribe_one, zhipu_key, t["image_bytes"]): t for t in tasks}
        for f in as_completed(futs):
            t = futs[f]
            try:
                r = f.result()
            except Exception as e:
                r = {"transcript": "", "ocr_flag": "uncertain",
                     "_layer": "转录失败：" + str(e)[:80]}
            results.append({**t, **r})
            done += 1
            if progress:
                progress(done, len(tasks))
    return results


def grade_student(deepseek_key, passage, questions, transcripts):
    """transcripts: {qid: text} → 经代码闸门校正后的 items"""
    user = P.build_grade_prompt(passage, questions, transcripts)
    raw = deepseek_chat(deepseek_key, P.GRADE_SYSTEM, user)
    obj, layer = robust_json(raw, list_key="items")
    items = obj.get("items") or []

    seen = {str(i.get("qid", "")).strip() for i in items}
    for q in questions:
        if str(q["qid"]) not in seen:
            items.append({"qid": str(q["qid"]), "score": 0, "error_tags": [],
                          "hit_points": [], "missed_points": q.get("points", []),
                          "why_wrong": "", "how_to_fix": "",
                          "teacher_note": "AI 未返回本题，需人工批改"})
    for it in items:
        it["_transcript"] = transcripts.get(str(it.get("qid", "")), "")
    items = P.finalize_scores(items, questions)
    for it in items:
        it["_layer"] = layer
    return items


def grade_batch(deepseek_key, passage, questions, per_student, progress=None):
    """per_student: {student_id: {qid: transcript}} → {student_id: items}"""
    out, done = {}, 0
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(grade_student, deepseek_key, passage, questions, tr): sid
                for sid, tr in per_student.items()}
        for f in as_completed(futs):
            sid = futs[f]
            try:
                out[sid] = f.result()
            except Exception as e:
                out[sid] = [{"qid": str(q["qid"]), "score": 0, "error_tags": [],
                             "hit_points": [], "missed_points": [], "why_wrong": "",
                             "how_to_fix": "", "teacher_note": "批改失败：" + str(e)[:60],
                             "_gate": ["调用异常"], "_need_review": True}
                            for q in questions]
            done += 1
            if progress:
                progress(done, len(per_student))
    return out


def extract_points(deepseek_key, raw_answer_text):
    """老师的参考答案原文 → 结构化给分点"""
    raw = deepseek_chat(deepseek_key, P.POINTS_SYSTEM, P.build_points_prompt(raw_answer_text))
    obj, layer = robust_json(raw, list_key="questions")
    return obj.get("questions") or [], layer


def diagnose_class(deepseek_key, passage, questions, class_rows):
    raw = deepseek_chat(deepseek_key, P.DIAGNOSE_SYSTEM,
                        P.build_diagnose_prompt(passage, questions, class_rows))
    obj, layer = robust_json(raw)
    return {"class_summary": obj.get("class_summary", ""),
            "top_issues": obj.get("top_issues", []),
            "praise": obj.get("praise", []),
            "_layer": layer}
