# -*- coding: utf-8 -*-
"""4 复核：按题横切 + 三级分诊 + 改给分点一键重判全班"""
import base64
import streamlit as st
import database as db
import engine as E
import prompts_yue as P

st.set_page_config(page_title="复核", page_icon="🔍", layout="wide")
st.markdown("<style>.block-container{padding-top:3.5rem}</style>", unsafe_allow_html=True)
st.title("🔍 复核")

aid = st.session_state.get("aid")
if not aid:
    st.warning("请先回首页选一份作业。")
    st.stop()
a = db.get_assignment(aid)
qrows = db.get_questions(aid)
if not qrows:
    st.stop()
roster = db.get_roster()

st.caption("按题横切：一屏只看一题的全班作答，判分标准统一，手最快。")
qids = [r["qid"] for r in qrows]
qid = st.selectbox("看第几题", qids,
                   format_func=lambda x: "第 " + x + " 题")
qrow = next(r for r in qrows if r["qid"] == qid)

with st.container(border=True):
    st.markdown("**题干**　" + (qrow["stem"] or ""))
    if qrow["requirement"]:
        st.markdown("**题型指令**　" + qrow["requirement"])
    st.markdown("**给分点**")
    for p in (qrow["points"] or []):
        st.markdown("- [" + str(p.get("score", 1)) + "分] " + str(p.get("text", "")))

grades = db.get_grades(aid, qid)
trans = {r["student_id"]: r for r in db.get_transcripts(aid) if r["qid"] == qid}
if not grades:
    st.info("这题还没有批改结果。")
    st.stop()

# ── 三级分诊 ──
maxs = qrow["max_score"]
must, spot, pass_ = [], [], []
for g in grades:
    sc = g["final_score"] if g["final_score"] is not None else g["score"]
    if g["need_review"] or (0 < sc < maxs):
        must.append(g)
    elif sc == maxs:
        spot.append(g)
    else:
        pass_.append(g)
c1, c2, c3 = st.columns(3)
c1.metric("必看（模糊分/闸门触发）", len(must))
c2.metric("满分抽查", len(spot))
c3.metric("零分", len(pass_))

view = st.radio("显示", ["必看", "满分抽查（10%）", "零分", "全部"], horizontal=True)
if view == "必看":
    show = must
elif view.startswith("满分"):
    show = spot[::max(1, len(spot) // max(1, len(spot) // 10 or 1))][:max(1, len(spot) // 10)]
elif view == "零分":
    show = pass_
else:
    show = grades

show_img = st.checkbox("同时显示原卷图片（慢一些）")

for g in show:
    sid = g["student_id"]
    name = roster.get(sid, {}).get("student_name", sid)
    t = trans.get(sid, {})
    with st.container(border=True):
        c1, c2 = st.columns([4, 1])
        with c1:
            st.markdown("**" + name + "**　`" + sid + "`"
                        + ("　⚠ 识别存疑" if t.get("ocr_flag") == "uncertain" else ""))
            st.text_area("AI 转录的学生答案", value=t.get("transcript") or "（空白）",
                         key="t_" + sid, height=80, disabled=True)
            miss = g["missed_points"] or []
            if miss:
                st.caption("漏：" + "；".join(str(m.get("text", m)) if isinstance(m, dict)
                                             else str(m) for m in miss))
            tags = g["error_tags"] or []
            if tags:
                st.caption("错因：" + "　".join(t2 + " " + P.ERROR_TAGS.get(t2, "") for t2 in tags))
            if g["why_wrong"]:
                st.caption("为什么错：" + g["why_wrong"])
            if g["gate_actions"]:
                st.warning("代码闸门：" + "；".join(g["gate_actions"]))
        with c2:
            cur = g["final_score"] if g["final_score"] is not None else g["score"]
            new = st.number_input("得分 /" + str(maxs), 0, maxs, int(cur or 0), key="s_" + sid)
            if new != cur:
                db.update_final_score(aid, sid, qid, int(new))
                st.toast(name + " 已改为 " + str(new) + " 分")
        if show_img:
            b64 = db.get_image(aid, sid, qid)
            if b64:
                st.image(base64.b64decode(b64), width=520)

# ── 改给分点后一键重判 ──
st.divider()
with st.container(border=True):
    st.subheader("发现给分点写得不对？改了可以一键重判全班")
    st.caption("转录已存档，重判只跑纯文本，几秒钟，不重跑图片。")
    ptxt = "\n".join((str(p.get("score", 1)) + "|" + str(p.get("text", "")))
                     for p in (qrow["points"] or []))
    newp = st.text_area("给分点（分值|内容）", value=ptxt, height=120)
    if st.button("💾 保存并重判本题全班", type="primary"):
        pts = []
        for line in newp.split("\n"):
            line = line.strip()
            if not line:
                continue
            if "|" in line:
                sc, tx = line.split("|", 1)
                try:
                    pts.append({"score": float(sc.strip()), "text": tx.strip()})
                    continue
                except ValueError:
                    pass
            pts.append({"score": 1, "text": line})
        db.save_questions(aid, [{"qid": qid, "stem": qrow["stem"], "max_score": maxs,
                                 "requirement": qrow["requirement"], "points": pts}])
        one = [{"qid": qid, "stem": qrow["stem"], "max_score": maxs,
                "requirement": qrow["requirement"], "points": pts}]
        per = {sid: {qid: (trans.get(sid, {}).get("transcript") or "")} for sid in
               {g["student_id"] for g in grades}}
        bar = st.progress(0.0)
        out = E.grade_batch(st.secrets["claude_key"], a.get("passage") or "", one, per,
                            progress=lambda d, t: bar.progress(d / t))
        for sid, items in out.items():
            for it in items:
                db.save_grade(aid, sid, it)
        st.success("已重判 " + str(len(out)) + " 人。")
        st.rerun()
