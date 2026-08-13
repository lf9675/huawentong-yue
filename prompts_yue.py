# -*- coding: utf-8 -*-
"""华文通·理 —— prompt 与字段契约

四套 prompt：
  A build_points_prompt   拆给分点     Claude
  B TRANSCRIBE_PROMPT     手写转录     Gemini 2.5 Flash
  C build_grade_prompt    评分归因     Claude
  D build_diagnose_prompt 班级诊断     Claude

纪律（沿用作文引擎的教训）：
  凡是硬性封顶，一律在 finalize_scores() 里用代码执行。
  AI 会嘴上承认规则、手上照样给分——prompt 只负责说理，代码负责兜底。

ENGINE_VERSION 只在评分规则变动时更新，结构重构不动它。
"""

ENGINE_VERSION = "2026-08-13"

# ════════════════════════════════════════════════════════════
# 错因标签体系（固定十类，不允许 AI 自创，否则全班统计失去意义）
# ════════════════════════════════════════════════════════════
ERROR_TAGS = {
    "E01": "空白／未作答",
    "E02": "答非所问，没回应题干要求",
    "E03": "照抄原文，未作转换归纳",
    "E04": "要点不全，漏给分点",
    "E05": "曲解文意，理解方向错误",
    "E06": "关键词句理解错误",
    "E07": "概括过于笼统，不够具体",
    "E08": "脱离文本的臆断，无依据",
    "E09": "违反题型指令（用自己的话／举例数量／字数）",
    "E10": "表达不清，语病影响达意",
}

# 每类错因的教学对策：讲评怎么讲、练什么。学生端和讲评底稿共用这一份，
# 一次维护，两处生效。
TAG_REMEDY = {
    "E02": {"why": "读题时只抓到话题词，没抓到题干的提问方向（为什么／怎样／哪些）。",
            "fix": "答题前把题干的疑问词圈出来，答案第一句必须正面回答那个疑问词。",
            "drill": "给题干，只写「答案第一句」，不写完整答案。"},
    "E03": {"why": "把「找到出处」当成了「答完了」，缺少转述这一步。",
            "fix": "找到原文句后，合上原文，用自己的话复述一遍再写下来。",
            "drill": "给一句原文，限定不得重复其中任何四字连用，改写成答案。"},
    "E04": {"why": "看到一个点就停笔，没意识到分值在提示点数。",
            "fix": "看分值定点数：3 分至少三点，4 分至少两点带说明。",
            "drill": "给答案，数一数它有几个得分点，缺的补上。"},
    "E05": {"why": "凭对文章的整体印象作答，没回到具体段落核对。",
            "fix": "答题前先定位到相关段落，逐句读完再答。",
            "drill": "给一个错误理解，指出它错在原文哪一句。"},
    "E06": {"why": "关键词理解偏差，导致整题方向偏。",
            "fix": "遇到关键词先在原文语境里推断词义，不用平时的常用义。",
            "drill": "词语在文中的意思 vs 常用义，辨析选择。"},
    "E07": {"why": "用了「很好」「不错」这类空词，没有落到文本细节。",
            "fix": "每个概括后面必须跟「例如……」，用原文细节支撑。",
            "drill": "把笼统的答案改写成带细节的答案。"},
    "E08": {"why": "把自己的生活经验当成文章内容写进了答案。",
            "fix": "答案里的每句话都要能在原文找到依据，找不到就删。",
            "drill": "判断题：这句话是文章说的，还是你以为的？"},
    "E09": {"why": "没看到题干的附加要求，或看到了但答题时忘了。",
            "fix": "题干的「用自己的话」「举两个例子」「不超过X字」先圈出来，写完回头核对。",
            "drill": "只做一件事：把题干里的附加要求圈出来。"},
    "E10": {"why": "想法是对的，但句子没写清楚，阅卷员看不出你答到了点。",
            "fix": "一句话说一件事，写完自己读一遍，读不通就断句重写。",
            "drill": "把一个长病句拆成两三个短句。"},
}

# ── 字段契约 ──
# B 转录输出
TRANSCRIBE_FIELDS = ["transcript", "ocr_flag"]
# C 评分输出（顶层）
GRADE_FIELDS = ["qid", "score", "hit_points", "missed_points",
                "error_tags", "why_wrong", "how_to_fix", "teacher_note"]
# D 诊断输出（顶层）
DIAGNOSE_FIELDS = ["class_summary", "top_issues", "praise"]


# ════════════════════════════════════════════════════════════
# A · 拆给分点
# ════════════════════════════════════════════════════════════
POINTS_SYSTEM = (
    "你是新加坡中学高级华文（HCL）阅读理解的资深阅卷员。\n"
    "老师会给你一份参考答案，请把它拆成一条条独立的「给分点」，供机器逐点判断命中与否。\n"
    "\n"
    "拆点规则：\n"
    "1. 一个给分点 = 一件可以独立判断对错的事。不要把两件事写在一条里。\n"
    "2. 用陈述句写，不要写成「答出XX即可」这种笼统的话——那会让评分变松。\n"
    "3. 分值加起来必须等于该题满分。参考答案里已标注分值的（如「(1分)」），照搬。\n"
    "4. 参考答案里若列出多于所需的备选点（如「以下任选两个」），全部列出并在\n"
    "   requirement 里注明「任选N点」。\n"
    "5. 题干若有附加要求（用自己的话／举两个例子／不超过X字），写进 requirement。\n"
    "\n"
    "只输出 JSON，不要代码块，不要解释：\n"
    '{"questions":[{"qid":"26","stem":"题干原文","max_score":3,'
    '"requirement":"用自己的话作答；无则留空",'
    '"points":[{"text":"给分点内容","score":1}]}]}'
)


def build_points_prompt(raw_answer_text: str) -> str:
    return ("以下是老师提供的题目与参考答案原文，请按规则拆成给分点：\n\n"
            + raw_answer_text.strip())


# ════════════════════════════════════════════════════════════
# B · 手写转录（规则沿用作文引擎已验证的那套）
# ════════════════════════════════════════════════════════════
TRANSCRIBE_PROMPT = (
    "这是新加坡中学生用触控笔在平板上手写的华文阅读理解答案。请逐字转写。\n"
    "\n"
    "1. 一字不改，原样转写。不要更正错别字、病句、标点——这些是批改对象。\n"
    "2. 不要补全学生没写完的句子，不要替他润色。\n"
    "3. 依上下文合理推断字形潦草的字，写出最可能的那个字。\n"
    "4. 实在推断不出的单字用〇占位，不要跳过。\n"
    "5. 学生有涂改的，以最终保留的内容为准；有箭头补写的，按箭头指向拼回顺序。\n"
    "6. 图片里的印刷体（题目、选项、页码、表格标题）一律不要转写，只转写手写内容。\n"
    "7. 姓名、班级、日期等抬头信息一律跳过。\n"
    "8. 该题若完全空白，transcript 输出空字符串。\n"
    "\n"
    "只输出 JSON，不要代码块，不要解释：\n"
    '{"transcript":"逐字转写的手写内容","ocr_flag":"clear 或 uncertain"}\n'
    "转写中出现过〇，ocr_flag 填 uncertain；否则填 clear。"
)


# ════════════════════════════════════════════════════════════
# C · 评分归因
# ════════════════════════════════════════════════════════════
GRADE_SYSTEM = (
    "你是新加坡中学高级华文（HCL）阅读理解的资深阅卷员，也是一位会讲道理的老师。\n"
    "你要做两件事：按给分点判分，并说清楚学生为什么错、怎么改。\n"
    "\n"
    "【判分原则】\n"
    "1. 语言从宽、内容从严：句子不通顺、有错别字，只要意思清楚就照给分；\n"
    "   漏给分点、照抄原文，照扣。\n"
    "2. 逐点判断，不看字数。学生用自己的措辞说出同一意思，算命中。\n"
    "3. 题干指令是硬要求：\n"
    "   - 要求「用自己的话」而学生整句照抄原文 → 该点不给分，标 E09 与 E03\n"
    "   - 要求「举两个例子」而只举一个 → 按比例给分，标 E09\n"
    "   - 有字数上限而明显超出 → 标 E09（不额外扣分，由老师定夺）\n"
    "4. 只依据学生写出来的内容判分，不脑补他「大概是想说」。\n"
    "5. 转写里的〇是识别不出的字，按上下文合理推断其意图，不因为有〇就扣分。\n"
    "6. transcript 为空 → score 为 0，error_tags 为 [\"E01\"]。\n"
    "\n"
    "【错因标签】只能从下列代码中选，不得自创，可多选；全对则空数组：\n"
    + "\n".join("  " + k + " = " + v for k, v in ERROR_TAGS.items()) + "\n"
    "\n"
    "【why_wrong 和 how_to_fix 是这个平台的核心价值，必须认真写】\n"
    "- why_wrong：指出学生的思路在哪一步走岔了，要具体到这道题这个学生。\n"
    "  写「没答到点」是废话；写「他找到了原文出处，但直接抄了下来，没有转成自己的话」才是诊断。\n"
    "- how_to_fix：给一个下次能照做的动作，不是「要认真审题」这类口号。\n"
    "- 两项都用初中生看得懂的话，不要用「维度」「升华」这类术语。\n"
    "- 全对时 why_wrong 留空，how_to_fix 写一句这道题他做得好在哪里。\n"
    "\n"
    "只输出 JSON，不要代码块，不要解释：\n"
    '{"items":[{"qid":"26","score":2,'
    '"hit_points":["命中的给分点原文"],"missed_points":["漏掉的给分点原文"],'
    '"error_tags":["E04"],"why_wrong":"...","how_to_fix":"...",'
    '"teacher_note":"给老师看的判分依据，一句话"}]}'
)


def build_grade_prompt(passage: str, questions: list, transcripts: dict) -> str:
    """questions: [{qid, stem, max_score, requirement, points:[{text,score}]}]
    transcripts: {qid: 转写文本}"""
    parts = ["【阅读语段原文】", passage.strip(), ""]
    for q in questions:
        qid = str(q["qid"])
        parts.append("─" * 40)
        parts.append("题号 " + qid + "（满分 " + str(q["max_score"]) + " 分）")
        parts.append("题干：" + str(q.get("stem", "")))
        req = str(q.get("requirement", "")).strip()
        if req:
            parts.append("题型指令（硬性要求）：" + req)
        parts.append("给分点：")
        for p in q.get("points", []):
            parts.append("  - [" + str(p.get("score", 1)) + "分] " + str(p.get("text", "")))
        parts.append("学生作答（手写转写，〇为识别不出的字）：")
        parts.append("  " + (transcripts.get(qid, "").strip() or "（空白，未作答）"))
        parts.append("")
    parts.append("请逐题判分，items 必须覆盖上面全部题号，一题都不能少。")
    return "\n".join(parts)


# ════════════════════════════════════════════════════════════
# D · 班级诊断（全班共一次调用）
# ════════════════════════════════════════════════════════════
DIAGNOSE_SYSTEM = (
    "你是新加坡中学高级华文的教研组长，正在帮一位老师准备阅读理解讲评课。\n"
    "老师会给你全班的批改汇总。请产出一份可以直接拿去上课的讲评底稿。\n"
    "\n"
    "【结构，严格照这个顺序】对每个突出问题：\n"
    "  主要问题 → 例子（学生真实错答）→ 为什么错 → 怎么改 → 现场练习\n"
    "\n"
    "【要求】\n"
    "1. 只取人数最多的 3 个问题，按人次排序。不要面面俱到，讲评课讲不完。\n"
    "2. 例子必须用老师给的真实学生错答原文，不要自己编。\n"
    "   学生姓名已经替换成编号，不要在输出里出现任何人名。\n"
    "3. 「怎么改」要给出一个学生下次能照做的具体动作。\n"
    "4. 现场练习要能在课堂上 3 分钟内做完，且能自动判对错\n"
    "   （选择题、判断题、或有唯一正确改法的改写题），不要开放式问答。\n"
    "5. praise 挑 2-3 条全班答得好的句子做正面示范，注明编号。\n"
    "6. 全部用老师和学生看得懂的话，不用教研术语。\n"
    "\n"
    "只输出 JSON，不要代码块，不要解释：\n"
    '{"class_summary":"两三句话概括全班这次的整体表现和最该补的能力",'
    '"top_issues":[{"tag":"E03","title":"问题的一句话标题",'
    '"affected":21,"example_quote":"学生原答案","example_qid":"27",'
    '"why":"为什么错","fix":"怎么改",'
    '"drill":{"question":"练习题干","options":["A...","B..."],'
    '"answer":"A","explain":"为什么选A"}}],'
    '"praise":[{"student_id":"A3F2C1","qid":"28","quote":"答得好的原句",'
    '"reason":"好在哪里"}]}'
)


def build_diagnose_prompt(passage: str, questions: list, class_rows: list) -> str:
    """class_rows: [{student_id, qid, score, max_score, error_tags, transcript}]"""
    from collections import Counter
    tag_count = Counter()
    for r in class_rows:
        for t in (r.get("error_tags") or []):
            tag_count[t] += 1

    parts = ["【阅读语段原文】", passage.strip(), "", "【题目与给分点】"]
    for q in questions:
        parts.append("题 " + str(q["qid"]) + "（" + str(q["max_score"]) + "分）："
                     + str(q.get("stem", "")))
        for p in q.get("points", []):
            parts.append("   - " + str(p.get("text", "")))
    parts.append("")
    parts.append("【全班错因统计】共 "
                 + str(len({r["student_id"] for r in class_rows})) + " 名学生")
    for tag, n in tag_count.most_common():
        parts.append("  " + tag + " " + ERROR_TAGS.get(tag, "") + "：" + str(n) + " 人次")
    parts.append("")
    parts.append("【学生真实作答样本（供你挑选错例，姓名已替换为编号）】")
    for r in class_rows:
        txt = (r.get("transcript") or "").strip()
        if not txt:
            continue
        parts.append("[" + r["student_id"] + " 题" + str(r["qid"]) + " 得分"
                     + str(r.get("score")) + "/" + str(r.get("max_score"))
                     + " 错因" + ",".join(r.get("error_tags") or ["无"]) + "] " + txt)
    return "\n".join(parts)


# ════════════════════════════════════════════════════════════
# 代码闸门：不信 AI 的自觉，硬规则一律在这里执行
# ════════════════════════════════════════════════════════════
def finalize_scores(items: list, questions: list) -> list:
    """对 AI 返回的评分做机械校正。返回校正后的 items，并在 _gate 记录动作。"""
    qmap = {str(q["qid"]): q for q in questions}
    out = []
    for it in items:
        qid = str(it.get("qid", ""))
        q = qmap.get(qid)
        if q is None:
            continue
        gate = []
        maxs = int(q.get("max_score", 1))

        # 闸门1：分数越界
        try:
            score = int(round(float(it.get("score") or 0)))
        except (TypeError, ValueError):
            score, gate = 0, gate + ["分数非数字，归零待人工"]
        if score > maxs:
            score, _ = maxs, gate.append("超过满分，截到 " + str(maxs))
        if score < 0:
            score, _ = 0, gate.append("负分，归零")

        # 闸门2：空白作答必须 0 分（AI 有时会对空白给同情分）
        if not (it.get("_transcript") or "").strip():
            if score != 0:
                gate.append("空白却给了 " + str(score) + " 分，归零")
            score = 0
            it["error_tags"] = ["E01"]

        # 闸门3：错因标签白名单，剔除自创标签
        tags = [t for t in (it.get("error_tags") or []) if t in ERROR_TAGS]
        dropped = set(it.get("error_tags") or []) - set(tags)
        if dropped:
            gate.append("剔除自创标签 " + ",".join(sorted(dropped)))

        # 闸门4：满分不该带错因；非满分且无错因，补 E04
        if score == maxs and tags:
            gate.append("满分却标了错因，清空")
            tags = []
        if score < maxs and not tags:
            tags = ["E04"]
            gate.append("扣分但无错因，补 E04")

        # 闸门5：命中点数与分数明显矛盾时，标记待人工（不擅自改分）
        hit = it.get("hit_points") or []
        if score == 0 and hit:
            gate.append("判0分却列了命中点，标记待复核")

        it["score"] = score
        it["error_tags"] = tags
        it["_gate"] = gate
        it["_need_review"] = bool(gate) or 0 < score < maxs
        out.append(it)
    return out
