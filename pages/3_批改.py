# -*- coding: utf-8 -*-
"""3 批改：转录（Gemini）→ 评分归因（Claude），并发 + 断点续跑"""
import base64
import streamlit as st
import database as db
import engine as E

st.set_page_config(page_title="批改", page_icon="🤖", layout="wide")
st.markdown("<style>.block-container{padding-top:3.5rem}</style>", unsafe_allow_html=True)
st.title("🤖 批改")

aid = st.session_state.get("aid")
if not aid:
    st.warning("请先回首页选一份作业。")
    st.stop()
a = db.get_assignment(aid)
qs = db.get_questions(aid)
questions = [{"qid": r["qid"], "stem": r["stem"], "max_score": r["max_score"],
              "requirement": r["requirement"], "points": r["points"]} for r in qs]
subs = db.list_submitted_students(aid)
if not subs:
    st.warning("还没有导入学生作答。先去「2 导入」。")
    st.stop()

done_t = {(r["student_id"], r["qid"]) for r in db.get_transcripts(aid)}
done_g = db.graded_students(aid)
c1, c2, c3 = st.columns(3)
c1.metric("学生", len(subs))
c2.metric("已转录", len(done_t))
c3.metric("已评分", len(done_g))
st.caption("两步都可以断点续跑：中途断了再点一次，只跑没做完的。")

# ── 第一步：转录 ──
with st.container(border=True):
    st.subheader("第一步　转录手写（Gemini）")
    st.caption("只跑一次，结果永久存档。以后改给分点重判，不用再跑这一步。")
    tasks = []
    for sid in subs:
        for r in db.q("""select qid, image_b64, ink_ratio from page_images
                         where assignment_id=%s and student_id=%s""", (aid, sid)):
            if (sid, r["qid"]) in done_t:
                continue
            if r["ink_ratio"] is not None and 0 <= r["ink_ratio"] < 0.0008:
                db.save_transcript(aid, sid, r["qid"], "", "clear")  # 空白，不花钱
                continue
            tasks.append({"student_id": sid, "qid": r["qid"],
                          "image_bytes": base64.b64decode(r["image_b64"])})
    st.write("待转录 " + str(len(tasks)) + " 题（未作答的已自动跳过，不消耗调用）")
    if tasks and st.button("▶ 开始转录", type="primary"):
        bar, log = st.progress(0.0), st.empty()
        res = E.transcribe_batch(st.secrets["gemini_key"], tasks,
                                 progress=lambda d, t: (bar.progress(d / t),
                                                        log.write("转录 " + str(d) + "/" + str(t))))
        fail = 0
        for r in res:
            if r["_layer"].startswith("转录失败"):
                fail += 1
            db.save_transcript(aid, r["student_id"], r["qid"], r["transcript"], r["ocr_flag"])
        st.success("转录完成。失败 " + str(fail) + " 题（再点一次会重试）。")
        st.rerun()

# ── 第二步：评分 ──
with st.container(border=True):
    st.subheader("第二步　评分与归因（Claude）")
    st.caption("每个学生一次调用。纯文本，便宜，改了给分点可以随时重判。")
    trans = db.get_transcripts(aid)
    per_student = {}
    for r in trans:
        per_student.setdefault(r["student_id"], {})[r["qid"]] = r["transcript"] or ""

    mode = st.radio("范围", ["只批没批过的", "全部重判（改了给分点后用）"], horizontal=True)
    todo = {s: t for s, t in per_student.items()
            if mode.startswith("全部") or s not in done_g}
    st.write("待评分 " + str(len(todo)) + " 人")
    if todo and st.button("▶ 开始评分", type="primary"):
        bar, log = st.progress(0.0), st.empty()
        out = E.grade_batch(st.secrets["claude_key"], a.get("passage") or "",
                            questions, todo,
                            progress=lambda d, t: (bar.progress(d / t),
                                                   log.write("评分 " + str(d) + "/" + str(t))))
        flagged = 0
        for sid, items in out.items():
            for it in items:
                if it.get("_need_review"):
                    flagged += 1
                db.save_grade(aid, sid, it)
        st.success("评分完成。其中 " + str(flagged) + " 题被标记为待复核。到左侧「4 复核」。")
        st.rerun()
