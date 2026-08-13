# -*- coding: utf-8 -*-
"""2 导入：吃 ZIP/PDF → 切分 → 页题映射 → 中位底图 → 入库"""
import base64
import streamlit as st
import database as db
import splitter as S

st.set_page_config(page_title="导入", page_icon="📥", layout="wide")
st.markdown("<style>.block-container{padding-top:3.5rem}</style>", unsafe_allow_html=True)
st.title("📥 导入学生作答")

aid = st.session_state.get("aid")
if not aid:
    st.warning("请先回首页选一份作业。")
    st.stop()
a = db.get_assignment(aid)
qs = db.get_questions(aid)
if not qs:
    st.warning("这份作业还没有题目。先去「1 出题」。")
    st.stop()
st.caption("当前作业：" + a["title"] + "　共 " + str(len(qs)) + " 题")

st.info("在 Classkick 里选 **Export All** 导出整班学生作业，会得到一个 ZIP（一人一个 PDF）。"
        "直接把 ZIP 传上来即可，文件名叫什么都行——学生姓名在 PDF 文字层里。")

up = st.file_uploader("整班 ZIP，或单个 PDF（可多选）", type=["zip", "pdf"],
                      accept_multiple_files=True)

if up and st.button("① 解析文件", type="primary"):
    rows = []
    prog = st.progress(0.0)
    for i, f in enumerate(up):
        try:
            rows.extend(S.load_upload(f.getvalue(), f.name))
        except Exception as e:
            st.error(f.name + " 解析失败：" + str(e)[:120])
        prog.progress((i + 1) / len(up))
    st.session_state.parsed = S.group_by_student(rows)
    st.rerun()

students = st.session_state.get("parsed")
if not students:
    st.stop()

# ── 切分结果自检 ──
with st.container(border=True):
    st.subheader("② 切分结果（请扫一眼确认没串号）")
    bad = [s for s in students.values() if not s["complete"]]
    c1, c2, c3 = st.columns(3)
    c1.metric("学生数", len(students))
    c2.metric("页数不全", len(bad))
    c3.metric("班级", "、".join(sorted({s["class_code"] for s in students.values() if s["class_code"]}) or {"未标"}))
    if bad:
        st.warning("以下学生页数不全，导出可能不完整：" +
                   "、".join(s["student_name"] for s in bad))
    names = sorted(students.items(), key=lambda kv: kv[1]["student_name"])
    show = [names[0]] + ([names[-1]] if len(names) > 1 else [])
    cols = st.columns(len(show))
    for col, (sid, s) in zip(cols, show):
        first = s["pages"][min(s["pages"])]
        col.image(first, caption=s["student_name"] + "（" + sid + "）第一页", width=260)

# ── 页题映射 ──
with st.container(border=True):
    st.subheader("③ 第几页对应第几题")
    st.caption("Classkick 一页一 slide。语段页选「不是题目」。一页多题的情况，"
               "把同一题号填给多页也可以（会拼在一起识别）。")
    all_pages = sorted({p for s in students.values() for p in s["pages"]})
    saved = a.get("page_map") or {}
    opts = ["（不是题目）"] + [str(q["qid"]) for q in qs]
    page_map = {}
    cols = st.columns(min(6, len(all_pages)))
    for i, pno in enumerate(all_pages):
        default = saved.get(str(pno), "（不是题目）")
        idx = opts.index(default) if default in opts else 0
        sel = cols[i % len(cols)].selectbox("第 " + str(pno) + " 页", opts, index=idx,
                                            key="pm%d" % pno)
        if sel != "（不是题目）":
            page_map[str(pno)] = sel
    missing = [str(q["qid"]) for q in qs if str(q["qid"]) not in page_map.values()]
    if missing:
        st.warning("还没有页面对应这些题：" + "、".join(missing))

# ── 入库 ──
if st.button("④ 确认并入库", type="primary", disabled=not page_map):
    db.set_page_map(aid, page_map)
    db.upsert_roster([{"student_id": sid, "student_name": s["student_name"],
                       "class_code": s["class_code"]} for sid, s in students.items()])

    prog, log = st.progress(0.0), st.empty()
    # 中位底图：同一页取全班像素中位数 = 干净题目背景
    bgs = {}
    for pno in sorted({int(p) for p in page_map}):
        imgs = [s["pages"][pno] for s in students.values() if pno in s["pages"]]
        bgs[pno] = S.median_background(imgs)
    ready = sum(1 for v in bgs.values() if v is not None)
    log.write("中位底图：" + str(ready) + "/" + str(len(bgs)) + " 页生成成功"
              + ("" if ready == len(bgs) else "（人数不足 5 的页面跳过，走原图识别）"))

    blank = 0
    for i, (sid, s) in enumerate(students.items()):
        for pno_s, qid in page_map.items():
            pno = int(pno_s)
            img = s["pages"].get(pno)
            if img is None:
                continue
            ink, ratio = S.ink_layer(img, bgs.get(pno))
            if not S.has_answer(ratio):
                blank += 1
            db.save_image(aid, sid, pno, qid,
                          base64.b64encode(S.prepare_for_ocr(img)).decode(),
                          ratio if ratio is not None else -1)
        prog.progress((i + 1) / len(students))

    st.success("已入库 " + str(len(students)) + " 名学生。检测到 " + str(blank) +
               " 处未作答（零 API 成本判定，批改时会自动跳过）。到左侧「3 批改」。")
    st.session_state.pop("parsed", None)
