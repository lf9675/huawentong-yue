# -*- coding: utf-8 -*-
"""5 报告：班级诊断、讲评底稿、Excel、生成学生端 HTML"""
import io
import json
import random
import string
import streamlit as st
import pandas as pd
import database as db
import engine as E
import prompts_yue as P

st.set_page_config(page_title="报告", page_icon="📊", layout="wide")
st.markdown("<style>.block-container{padding-top:3.5rem}</style>", unsafe_allow_html=True)
st.title("📊 班级报告与讲评底稿")

aid = st.session_state.get("aid")
if not aid:
    st.warning("请先回首页选一份作业。")
    st.stop()
a = db.get_assignment(aid)
qrows = db.get_questions(aid)
grades = db.get_grades(aid)
if not grades:
    st.info("还没有批改结果。")
    st.stop()
roster = db.get_roster()
trans = {(r["student_id"], r["qid"]): r for r in db.get_transcripts(aid)}
qmap = {r["qid"]: r for r in qrows}

rows = []
for g in grades:
    sc = g["final_score"] if g["final_score"] is not None else g["score"]
    rows.append({"编号": g["student_id"],
                 "姓名": roster.get(g["student_id"], {}).get("student_name", ""),
                 "题号": g["qid"], "得分": sc or 0,
                 "满分": qmap.get(g["qid"], {}).get("max_score", 0),
                 "错因": "、".join(g["error_tags"] or []),
                 "为什么错": g["why_wrong"] or "", "怎么改": g["how_to_fix"] or "",
                 "转录": (trans.get((g["student_id"], g["qid"])) or {}).get("transcript", "")})
detail = pd.DataFrame(rows)

per_q = detail.groupby("题号").agg(平均分=("得分", "mean"), 满分=("满分", "max"),
                                   人数=("编号", "count")).reset_index()
per_q["得分率%"] = (per_q["平均分"] / per_q["满分"] * 100).round(1)
per_q = per_q.sort_values("得分率%")

tagrows = []
for _, r in detail.iterrows():
    for t in str(r["错因"]).split("、"):
        if t.strip():
            tagrows.append({"错因": t.strip(), "说明": P.ERROR_TAGS.get(t.strip(), ""),
                            "题号": r["题号"], "编号": r["编号"]})
tags_df = pd.DataFrame(tagrows)

c1, c2 = st.columns(2)
c1.markdown("**各题得分率**（最低的排最前 = 该重讲的）")
c1.dataframe(per_q[["题号", "平均分", "满分", "得分率%", "人数"]],
             hide_index=True, use_container_width=True)
if not tags_df.empty:
    dist = tags_df.groupby(["错因", "说明"]).size().reset_index(name="人次") \
                  .sort_values("人次", ascending=False)
    c2.markdown("**全班错因分布**")
    c2.dataframe(dist, hide_index=True, use_container_width=True)

total = detail.pivot_table(index=["编号", "姓名"], columns="题号",
                           values="得分", aggfunc="sum").fillna(0)
total["总分"] = total.sum(axis=1)
total = total.sort_values("总分", ascending=False).reset_index()
st.markdown("**成绩总表**")
st.dataframe(total, hide_index=True, use_container_width=True)

buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as w:
    total.to_excel(w, "成绩总表", index=False)
    detail.to_excel(w, "逐题明细", index=False)
    per_q.to_excel(w, "各题得分率", index=False)
    if not tags_df.empty:
        tags_df.to_excel(w, "错因清单", index=False)
st.download_button("⬇ 下载 Excel", buf.getvalue(),
                   file_name=(a["title"] + "_报告.xlsx"),
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ── 班级诊断 ──
st.divider()
st.subheader("讲评底稿（全班一次调用生成）")
diag = db.get_diagnosis(aid)
if st.button("🤖 生成／重新生成讲评底稿", type="primary"):
    class_rows = [{"student_id": r["编号"], "qid": r["题号"], "score": r["得分"],
                   "max_score": r["满分"],
                   "error_tags": [t for t in str(r["错因"]).split("、") if t],
                   "transcript": r["转录"]} for _, r in detail.iterrows()]
    questions = [{"qid": r["qid"], "stem": r["stem"], "max_score": r["max_score"],
                  "requirement": r["requirement"], "points": r["points"]} for r in qrows]
    with st.spinner("生成中…"):
        diag = E.diagnose_class(st.secrets["deepseek_key"], a.get("passage") or "",
                                questions, class_rows)
    db.save_diagnosis(aid, diag)
    st.rerun()

if diag:
    st.info(diag.get("class_summary", ""))
    for i, iss in enumerate(diag.get("top_issues", []), 1):
        with st.container(border=True):
            st.markdown("### " + str(i) + ". " + str(iss.get("title", ""))
                        + "　（" + str(iss.get("affected", "")) + " 人次）")
            st.markdown("**学生原答案**（第 " + str(iss.get("example_qid", "")) + " 题）")
            st.code(str(iss.get("example_quote", "")), language=None)
            st.markdown("**为什么错**　" + str(iss.get("why", "")))
            st.markdown("**怎么改**　" + str(iss.get("fix", "")))
            d = iss.get("drill") or {}
            if d:
                st.markdown("**现场练习**　" + str(d.get("question", "")))
                for o in (d.get("options") or []):
                    st.markdown("- " + str(o))
                st.caption("答案：" + str(d.get("answer", "")) + "　" + str(d.get("explain", "")))
    if diag.get("praise"):
        with st.container(border=True):
            st.markdown("### 本班佳答欣赏")
            for p in diag["praise"]:
                st.markdown("`" + str(p.get("student_id", "")) + "` 第"
                            + str(p.get("qid", "")) + "题：" + str(p.get("quote", "")))
                st.caption(str(p.get("reason", "")))

# ── 生成学生端 ──
st.divider()
st.subheader("发给学生")
if st.button("🔑 生成取件码并导出学生端网页"):
    codes = db.get_codes(aid)
    for sid in {g["student_id"] for g in grades}:
        codes.setdefault(sid, "".join(random.choices(string.digits, k=6)))
    db.save_codes(aid, codes)

    payload = {"title": a["title"], "tags": P.ERROR_TAGS, "remedy": P.TAG_REMEDY,
               "questions": [{"qid": r["qid"], "stem": r["stem"],
                              "max_score": r["max_score"],
                              "points": r["points"]} for r in qrows],
               "students": {}}
    for g in grades:
        sid = g["student_id"]
        code = codes[sid]
        s = payload["students"].setdefault(code, {"items": []})
        s["items"].append({
            "qid": g["qid"],
            "score": g["final_score"] if g["final_score"] is not None else g["score"],
            "max": qmap.get(g["qid"], {}).get("max_score", 0),
            "transcript": (trans.get((sid, g["qid"])) or {}).get("transcript", ""),
            "missed": g["missed_points"] or [], "tags": g["error_tags"] or [],
            "why": g["why_wrong"] or "", "fix": g["how_to_fix"] or ""})

    tpl = open("student_template.html", encoding="utf-8").read()
    html = tpl.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
    st.download_button("⬇ 下载 student.html（传到 Netlify）", html.encode("utf-8"),
                       file_name="student.html", mime="text/html", type="primary")

    slip = pd.DataFrame([{"编号": s, "姓名": roster.get(s, {}).get("student_name", ""),
                          "取件码": c} for s, c in sorted(codes.items())])
    st.markdown("**取件码对照表**（打印后裁开发给学生，别群发）")
    st.dataframe(slip, hide_index=True, use_container_width=True)
    st.download_button("⬇ 下载取件码 CSV", slip.to_csv(index=False).encode("utf-8-sig"),
                       file_name="取件码.csv", mime="text/csv")
