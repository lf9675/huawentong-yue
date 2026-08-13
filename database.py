# -*- coding: utf-8 -*-
"""华文通·理 —— 数据库存取层（Supabase Postgres）

连接串放 Streamlit Secrets 的 db_url，用 Transaction Pooler（端口 6543）。
Direct 连接是 IPv6-only，Streamlit Cloud 连不上。
所有写入用 ON CONFLICT DO UPDATE，重跑不炸。
"""

import json

import psycopg2
import psycopg2.extras
import streamlit as st


@st.cache_resource
def _pool():
    return psycopg2.connect(st.secrets["db_url"], connect_timeout=10)


def _conn():
    c = _pool()
    if c.closed:
        _pool.clear()
        c = _pool()
    return c


def q(sql, params=None, fetch=True):
    with _conn().cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params or ())
        rows = cur.fetchall() if fetch and cur.description else []
    _conn().commit()
    return [dict(r) for r in rows]


# ── 花名册 ──
def upsert_roster(rows):
    """rows: [{student_id, student_name, class_code}]"""
    for r in rows:
        q("""insert into roster(student_id,student_name,class_code)
             values(%s,%s,%s)
             on conflict(student_id) do update
             set student_name=excluded.student_name,
                 class_code=excluded.class_code, updated_at=now()""",
          (r["student_id"], r["student_name"], r.get("class_code")), fetch=False)


def get_roster():
    return {r["student_id"]: r for r in q("select * from roster order by student_name")}


# ── 作业与题目 ──
def create_assignment(title, class_code, passage, engine_version):
    return q("""insert into assignments(title,class_code,passage,engine_version)
                values(%s,%s,%s,%s) returning id""",
             (title, class_code, passage, engine_version))[0]["id"]


def list_assignments():
    return q("select * from assignments order by created_at desc")


def get_assignment(aid):
    r = q("select * from assignments where id=%s", (aid,))
    return r[0] if r else None


def set_page_map(aid, page_map):
    q("update assignments set page_map=%s where id=%s",
      (json.dumps(page_map), aid), fetch=False)


def save_questions(aid, questions):
    for x in questions:
        q("""insert into questions(assignment_id,qid,stem,max_score,requirement,points)
             values(%s,%s,%s,%s,%s,%s)
             on conflict(assignment_id,qid) do update
             set stem=excluded.stem, max_score=excluded.max_score,
                 requirement=excluded.requirement, points=excluded.points""",
          (aid, str(x["qid"]), x.get("stem", ""), int(x.get("max_score", 1)),
           x.get("requirement", ""), json.dumps(x.get("points", []), ensure_ascii=False)),
          fetch=False)


def get_questions(aid):
    return q("select * from questions where assignment_id=%s order by qid", (aid,))


# ── 原图 ──
def save_image(aid, sid, page_no, qid, image_b64, ink_ratio):
    q("""insert into page_images(assignment_id,student_id,page_no,qid,image_b64,ink_ratio)
         values(%s,%s,%s,%s,%s,%s)
         on conflict(assignment_id,student_id,page_no) do update
         set qid=excluded.qid, image_b64=excluded.image_b64,
             ink_ratio=excluded.ink_ratio, created_at=now()""",
      (aid, sid, page_no, qid, image_b64, ink_ratio), fetch=False)


def get_image(aid, sid, qid):
    r = q("""select image_b64 from page_images
             where assignment_id=%s and student_id=%s and qid=%s limit 1""",
          (aid, sid, qid))
    return r[0]["image_b64"] if r else None


def list_submitted_students(aid):
    return [r["student_id"] for r in
            q("""select distinct student_id from page_images
                 where assignment_id=%s order by student_id""", (aid,))]


# ── 转录 ──
def save_transcript(aid, sid, qid, text, flag, corrected=False):
    q("""insert into transcripts(assignment_id,student_id,qid,transcript,ocr_flag,corrected)
         values(%s,%s,%s,%s,%s,%s)
         on conflict(assignment_id,student_id,qid) do update
         set transcript=excluded.transcript, ocr_flag=excluded.ocr_flag,
             corrected=excluded.corrected, updated_at=now()""",
      (aid, sid, qid, text, flag, corrected), fetch=False)


def get_transcripts(aid, sid=None):
    if sid:
        return q("""select * from transcripts where assignment_id=%s and student_id=%s""",
                 (aid, sid))
    return q("select * from transcripts where assignment_id=%s", (aid,))


# ── 评分 ──
def save_grade(aid, sid, item):
    q("""insert into grades(assignment_id,student_id,qid,score,final_score,
             hit_points,missed_points,error_tags,why_wrong,how_to_fix,
             teacher_note,gate_actions,need_review)
         values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
         on conflict(assignment_id,student_id,qid) do update
         set score=excluded.score, final_score=excluded.final_score,
             hit_points=excluded.hit_points, missed_points=excluded.missed_points,
             error_tags=excluded.error_tags, why_wrong=excluded.why_wrong,
             how_to_fix=excluded.how_to_fix, teacher_note=excluded.teacher_note,
             gate_actions=excluded.gate_actions, need_review=excluded.need_review,
             reviewed=false, updated_at=now()""",
      (aid, sid, str(item["qid"]), item["score"], item["score"],
       json.dumps(item.get("hit_points", []), ensure_ascii=False),
       json.dumps(item.get("missed_points", []), ensure_ascii=False),
       json.dumps(item.get("error_tags", []), ensure_ascii=False),
       item.get("why_wrong", ""), item.get("how_to_fix", ""),
       item.get("teacher_note", ""),
       json.dumps(item.get("_gate", []), ensure_ascii=False),
       bool(item.get("_need_review"))), fetch=False)


def update_final_score(aid, sid, qid, final_score):
    q("""update grades set final_score=%s, reviewed=true, updated_at=now()
         where assignment_id=%s and student_id=%s and qid=%s""",
      (final_score, aid, sid, qid), fetch=False)


def get_grades(aid, qid=None):
    if qid:
        return q("""select * from grades where assignment_id=%s and qid=%s
                    order by student_id""", (aid, qid))
    return q("select * from grades where assignment_id=%s order by student_id, qid", (aid,))


def graded_students(aid):
    return {r["student_id"] for r in
            q("select distinct student_id from grades where assignment_id=%s", (aid,))}


# ── 诊断与取件码 ──
def save_diagnosis(aid, payload):
    q("""insert into diagnoses(assignment_id,payload) values(%s,%s)
         on conflict(assignment_id) do update
         set payload=excluded.payload, created_at=now()""",
      (aid, json.dumps(payload, ensure_ascii=False)), fetch=False)


def get_diagnosis(aid):
    r = q("select payload from diagnoses where assignment_id=%s", (aid,))
    return r[0]["payload"] if r else None


def save_codes(aid, mapping):
    for sid, code in mapping.items():
        q("""insert into access_codes(assignment_id,student_id,code) values(%s,%s,%s)
             on conflict(assignment_id,student_id) do update set code=excluded.code""",
          (aid, sid, code), fetch=False)


def get_codes(aid):
    return {r["student_id"]: r["code"]
            for r in q("select * from access_codes where assignment_id=%s", (aid,))}
