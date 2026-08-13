# -*- coding: utf-8 -*-
"""华文通·理 —— 引擎调用层（无 Streamlit 依赖）

转录：Gemini 2.5 Flash（视觉，便宜，跑一次存档）
评分：Claude（判断力活）——必须用 messages.stream()，不用 create()
诊断：Claude，全班一次调用

注：2026-08-13 查证，Classkick 无公开 API，导入只能走手动导出 ZIP。
若日后开放，只需替换 splitter 的数据源，本文件不受影响。
"""

import base64
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
import google.generativeai as genai

import prompts_yue as P

TRANSCRIBE_MODEL = "gemini-2.5-flash"
GRADE_MODEL = "claude-sonnet-5"
MAX_TOKENS = 8000
CONCURRENCY = 6


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
# 调用
# ════════════════════════════════════════════════════════════
def _claude(api_key, system, user_text, model=GRADE_MODEL):
    client = anthropic.Anthropic(api_key=api_key)
    out = ""
    with client.messages.stream(model=model, max_tokens=MAX_TOKENS, system=system,
                                messages=[{"role": "user", "content": user_text}]) as s:
        for chunk in s.text_stream:
            out += chunk
    return out


def transcribe_one(gemini_key, image_bytes):
    """单题作答图 → {transcript, ocr_flag}"""
    genai.configure(api_key=gemini_key)
    model = genai.GenerativeModel(TRANSCRIBE_MODEL)
    resp = model.generate_content([
        P.TRANSCRIBE_PROMPT,
        {"mime_type": "image/jpeg", "data": base64.b64encode(image_bytes).decode()},
    ])
    obj, layer = robust_json(resp.text or "")
    return {"transcript": (obj.get("transcript") or "").strip(),
            "ocr_flag": obj.get("ocr_flag") or "uncertain",
            "_layer": layer}


def transcribe_batch(gemini_key, tasks, progress=None):
    """tasks: [{student_id, qid, image_bytes}] → [{student_id, qid, transcript, ocr_flag}]"""
    results, done = [], 0
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(transcribe_one, gemini_key, t["image_bytes"]): t for t in tasks}
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


def grade_student(claude_key, passage, questions, transcripts):
    """transcripts: {qid: text} → 经代码闸门校正后的 items"""
    user = P.build_grade_prompt(passage, questions, transcripts)
    raw = _claude(claude_key, P.GRADE_SYSTEM, user)
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


def grade_batch(claude_key, passage, questions, per_student, progress=None):
    """per_student: {student_id: {qid: transcript}} → {student_id: items}"""
    out, done = {}, 0
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(grade_student, claude_key, passage, questions, tr): sid
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


def extract_points(claude_key, raw_answer_text):
    """老师的参考答案原文 → 结构化给分点"""
    raw = _claude(claude_key, P.POINTS_SYSTEM, P.build_points_prompt(raw_answer_text))
    obj, layer = robust_json(raw, list_key="questions")
    return obj.get("questions") or [], layer


def diagnose_class(claude_key, passage, questions, class_rows):
    raw = _claude(claude_key, P.DIAGNOSE_SYSTEM,
                  P.build_diagnose_prompt(passage, questions, class_rows))
    obj, layer = robust_json(raw)
    return {"class_summary": obj.get("class_summary", ""),
            "top_issues": obj.get("top_issues", []),
            "praise": obj.get("praise", []),
            "_layer": layer}
