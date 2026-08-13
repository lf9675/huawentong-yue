# -*- coding: utf-8 -*-
"""华文通·理 —— 首页：选作业、看进度、查密钥"""
import streamlit as st
import database as db
import prompts_yue as P

st.set_page_config(page_title="华文通·理", page_icon="📖", layout="wide")
st.markdown("<style>.block-container{padding-top:3.5rem}</style>", unsafe_allow_html=True)

st.title("📖 华文通·理")
st.caption("阅读理解批改与错因诊断　引擎版本 " + P.ENGINE_VERSION)

# 密钥自检：三样缺一样都跑不动，早说比跑到一半炸好
need = {"db_url": "Supabase 连接串", "gemini_key": "Gemini（转录）",
        "claude_key": "Claude（评分与诊断）"}
miss = [v for k, v in need.items() if not st.secrets.get(k)]
if miss:
    st.error("Secrets 里还缺：" + "、".join(miss) + "。到 Streamlit Cloud → Settings → Secrets 补上。")
    st.stop()

try:
    rows = db.list_assignments()
except Exception as e:
    st.error("连不上数据库：" + str(e)[:200])
    st.info("检查 db_url 是否用了 Transaction Pooler（端口 6543）。Direct 连接是 IPv6-only，连不上。")
    st.stop()

st.success("数据库连接正常。")

with st.container(border=True):
    st.subheader("新建作业")
    c1, c2 = st.columns([2, 1])
    title = c1.text_input("作业名称", placeholder="例：中三 阅读理解 WA2《以德报怨》")
    klass = c2.text_input("班级（可留空）", placeholder="3A")
    if st.button("创建", type="primary", disabled=not title.strip()):
        aid = db.create_assignment(title.strip(), klass.strip(), "", P.ENGINE_VERSION)
        st.session_state.aid = aid
        st.success("已创建。到左侧「1 出题」继续。")
        st.rerun()

st.subheader("我的作业")
if not rows:
    st.info("还没有作业。先在上面创建一个。")
else:
    for a in rows:
        qs = db.get_questions(a["id"])
        subs = db.list_submitted_students(a["id"])
        graded = db.graded_students(a["id"])
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
            c1.markdown("**" + a["title"] + "**　" + (a["class_code"] or ""))
            c1.caption("创建于 " + str(a["created_at"])[:16])
            c2.metric("题数", len(qs))
            c3.metric("已交", len(subs))
            c4.metric("已批", len(graded))
            if st.button("打开", key="open_" + str(a["id"])):
                st.session_state.aid = a["id"]
                st.success("已选中：" + a["title"] + "　到左侧页面继续。")

if st.session_state.get("aid"):
    cur = db.get_assignment(st.session_state.aid)
    if cur:
        st.sidebar.success("当前作业\n\n" + cur["title"])
