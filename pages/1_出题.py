# -*- coding: utf-8 -*-
"""1 出题：贴语段、上传参考答案让 AI 拆给分点、复核入库"""
import streamlit as st
import database as db
import engine as E

st.set_page_config(page_title="出题", page_icon="✏️", layout="wide")
st.markdown("<style>.block-container{padding-top:3.5rem}</style>", unsafe_allow_html=True)
st.title("✏️ 出题与给分点")

aid = st.session_state.get("aid")
if not aid:
    st.warning("请先回首页选一份作业。")
    st.stop()
a = db.get_assignment(aid)
st.caption("当前作业：" + a["title"])

with st.container(border=True):
    st.subheader("阅读语段原文")
    st.caption("必填。AI 判断「照抄原文未转换」全靠它，不贴等于关掉 E03 这条错因。")
    passage = st.text_area("语段", value=a.get("passage") or "", height=220,
                           label_visibility="collapsed")
    if st.button("保存语段"):
        db.q("update assignments set passage=%s where id=%s", (passage, aid), fetch=False)
        st.success("已保存。")

with st.container(border=True):
    st.subheader("让 AI 拆给分点")
    st.caption("把你现成的参考答案原文粘进来，AI 拆成一条条可判断的给分点，你再改。")
    raw = st.text_area("参考答案原文", height=200,
                       placeholder="Q26 永平不满是因为年轻人问路时态度无礼（1分）连一句谢谢都没说（1分）……")
    if st.button("🤖 拆给分点", type="primary", disabled=not raw.strip()):
        with st.spinner("拆解中…"):
            qs, layer = E.extract_points(st.secrets["claude_key"], raw)
        if not qs:
            st.error("没拆出题目（" + layer + "）。检查参考答案格式，或手动在下面填。")
        else:
            st.session_state.draft_q = qs
            st.success("拆出 " + str(len(qs)) + " 题，请在下面复核后保存。")

st.subheader("题目与给分点（可直接编辑）")
existing = db.get_questions(aid)
draft = st.session_state.get("draft_q")
if draft:
    items = draft
elif existing:
    items = [{"qid": r["qid"], "stem": r["stem"], "max_score": r["max_score"],
              "requirement": r["requirement"] or "", "points": r["points"]} for r in existing]
else:
    items = [{"qid": "26", "stem": "", "max_score": 3, "requirement": "", "points": []}]

edited = []
for i, qi in enumerate(items):
    with st.container(border=True):
        c1, c2, c3 = st.columns([1, 1, 3])
        qid = c1.text_input("题号", value=str(qi.get("qid", "")), key="qid%d" % i)
        ms = c2.number_input("满分", 1, 20, int(qi.get("max_score", 1)), key="ms%d" % i)
        req = c3.text_input("题型指令（用自己的话／举两个例子／字数上限）",
                            value=qi.get("requirement", "") or "", key="req%d" % i)
        stem = st.text_area("题干", value=qi.get("stem", "") or "", key="stem%d" % i, height=68)
        ptxt = "\n".join((str(p.get("score", 1)) + "|" + str(p.get("text", "")))
                         for p in (qi.get("points") or []))
        newp = st.text_area("给分点（一行一个，格式：分值|内容）", value=ptxt,
                            key="pts%d" % i, height=110)
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
        tot = sum(p["score"] for p in pts)
        if pts and abs(tot - ms) > 0.01:
            st.warning("给分点合计 " + str(tot) + " 分，与满分 " + str(ms) + " 不符。")
        edited.append({"qid": qid, "stem": stem, "max_score": ms,
                       "requirement": req, "points": pts})

c1, c2 = st.columns(2)
if c1.button("➕ 加一题"):
    items.append({"qid": "", "stem": "", "max_score": 3, "requirement": "", "points": []})
    st.session_state.draft_q = items
    st.rerun()
if c2.button("💾 保存全部题目", type="primary"):
    valid = [x for x in edited if str(x["qid"]).strip()]
    db.save_questions(aid, valid)
    st.session_state.pop("draft_q", None)
    st.success("已保存 " + str(len(valid)) + " 题。到左侧「2 导入」上传学生作答。")
