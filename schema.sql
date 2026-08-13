-- 华文通·理 —— Supabase 建表脚本
-- 在 Supabase 控制台 → SQL Editor → 粘贴全文 → Run
-- 可重复执行（IF NOT EXISTS），不会覆盖已有数据

-- 1) 花名册：姓名只存在这里，永不进入 AI 请求
create table if not exists roster (
  student_id   text primary key,          -- 本地生成的 6 位编号
  student_name text not null,
  class_code   text,
  updated_at   timestamptz default now()
);

-- 2) 作业（一次测验 = 一条）
create table if not exists assignments (
  id            bigserial primary key,
  title         text not null,
  class_code    text,
  passage       text,                     -- 阅读语段原文
  page_map      jsonb default '{}'::jsonb, -- {"3":"26","4":"27"} 第几页=第几题
  engine_version text,
  created_at    timestamptz default now()
);

-- 3) 题目与给分点
create table if not exists questions (
  id            bigserial primary key,
  assignment_id bigint references assignments(id) on delete cascade,
  qid           text not null,
  stem          text,
  max_score     int not null default 1,
  requirement   text,
  points        jsonb default '[]'::jsonb, -- [{"text":"...","score":1}]
  unique (assignment_id, qid)
);

-- 4) 作答原图（默认保留 30 天，见文末清理函数）
create table if not exists page_images (
  id            bigserial primary key,
  assignment_id bigint references assignments(id) on delete cascade,
  student_id    text,
  page_no       int,
  qid           text,
  image_b64     text,                     -- JPEG base64
  ink_ratio     real,                     -- 墨迹占比，0 = 没作答
  created_at    timestamptz default now(),
  unique (assignment_id, student_id, page_no)
);

-- 5) 转录
create table if not exists transcripts (
  assignment_id bigint references assignments(id) on delete cascade,
  student_id    text,
  qid           text,
  transcript    text,
  ocr_flag      text,                     -- clear / uncertain
  corrected     boolean default false,    -- 学生自己更正过
  updated_at    timestamptz default now(),
  primary key (assignment_id, student_id, qid)
);

-- 6) 评分与归因
create table if not exists grades (
  assignment_id bigint references assignments(id) on delete cascade,
  student_id    text,
  qid           text,
  score         int,
  final_score   int,                      -- 老师复核后的分，导出用这个
  hit_points    jsonb default '[]'::jsonb,
  missed_points jsonb default '[]'::jsonb,
  error_tags    jsonb default '[]'::jsonb,
  why_wrong     text,
  how_to_fix    text,
  teacher_note  text,
  gate_actions  jsonb default '[]'::jsonb, -- 代码闸门做过的动作
  need_review   boolean default false,
  reviewed      boolean default false,
  updated_at    timestamptz default now(),
  primary key (assignment_id, student_id, qid)
);

-- 7) 班级诊断（全班一次调用的产物）
create table if not exists diagnoses (
  assignment_id bigint primary key references assignments(id) on delete cascade,
  payload       jsonb,
  created_at    timestamptz default now()
);

-- 8) 学生取件码
create table if not exists access_codes (
  assignment_id bigint references assignments(id) on delete cascade,
  student_id    text,
  code          text not null,
  primary key (assignment_id, student_id)
);

create index if not exists idx_grades_assignment on grades(assignment_id);
create index if not exists idx_trans_assignment  on transcripts(assignment_id);
create index if not exists idx_img_created       on page_images(created_at);

-- 原图 30 天清理：定期在 SQL Editor 跑一次，或用 pg_cron 自动
-- select purge_old_images();
create or replace function purge_old_images() returns int as $$
declare n int;
begin
  delete from page_images where created_at < now() - interval '30 days';
  get diagnostics n = row_count;
  return n;
end;
$$ language plpgsql;
