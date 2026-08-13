# -*- coding: utf-8 -*-
"""华文通·理 —— 切分与图像处理引擎（无 Streamlit 依赖，可单独测试）

职责：
  1. 吃 ZIP / 单个 PDF，解析出「学生 → 页面图片」
  2. 从 PDF 文字层读学生姓名、页码、班级码（姓名只留在本地）
  3. 全班同页取中位数 → 干净题目底图 → 学生页减底图 = 纯笔迹层

设计依据（2026-08-13 实测 Classkick 导出样本）：
  - 每页文字层含 "Student: X" / "Page N / M" / "Class Code: XXX"
  - 每页恰好内嵌 1 张合成图（题目+笔迹已烧录），可无损直取
  - 分辨率不固定（Classkick 客服确认无文档保证），一律实测，禁止写死
"""

import hashlib
import io
import re
import zipfile

import numpy as np
import pymupdf
from PIL import Image

# ── 文字层解析规则 ──
RE_STUDENT = re.compile(r"Student:\s*(.+)")
RE_PAGE = re.compile(r"Page\s*(\d+)\s*/\s*(\d+)")
RE_CLASS = re.compile(r"Class\s*Code:\s*(.+)")   # 班级码含空格（实测 "DLG 4OA"），取整行

MIN_WIDTH_FOR_OCR = 1200   # 低于这个宽度先放大再送识别
MIN_STUDENTS_FOR_BG = 5    # 少于这么多人，中位底图不可靠，跳过


def local_student_id(name: str, salt: str = "hwt") -> str:
    """姓名 → 稳定的本地编号。姓名永不进入 AI 请求（PDPA）。"""
    return hashlib.md5((salt + name.strip().upper()).encode("utf-8")).hexdigest()[:6].upper()


def _page_image(doc, page) -> Image.Image:
    """取该页内嵌的合成图；没有内嵌图时回退到整页渲染（不常见，但不能崩）。"""
    imgs = page.get_images(full=True)
    if imgs:
        raw = doc.extract_image(imgs[0][0])
        return Image.open(io.BytesIO(raw["image"])).convert("RGB")
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


def parse_pdf_bytes(pdf_bytes: bytes, source_name: str = "") -> list:
    """解析一个 PDF，返回 [{student_name, student_id, class_code, page_no,
    total_pages, image, source}]，按页码排序。"""
    out = []
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page in doc:
            text = page.get_text()
            m_stu, m_pg, m_cls = (RE_STUDENT.search(text), RE_PAGE.search(text),
                                  RE_CLASS.search(text))
            name = m_stu.group(1).strip() if m_stu else (source_name or "未知学生")
            page_no = int(m_pg.group(1)) if m_pg else page.number + 1
            total = int(m_pg.group(2)) if m_pg else doc.page_count
            out.append({
                "student_name": name,
                "student_id": local_student_id(name),
                "class_code": m_cls.group(1).strip() if m_cls else "",
                "page_no": page_no,
                "total_pages": total,
                "image": _page_image(doc, page),
                "source": source_name,
            })
    finally:
        doc.close()
    out.sort(key=lambda r: r["page_no"])
    return out


def load_upload(file_bytes: bytes, filename: str) -> list:
    """吃 ZIP（整班导出）或单个 PDF。返回全部页记录。"""
    rows = []
    if filename.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            for info in zf.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".pdf"):
                    continue
                stem = info.filename.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                rows.extend(parse_pdf_bytes(zf.read(info), stem))
    else:
        stem = filename.rsplit(".", 1)[0]
        rows.extend(parse_pdf_bytes(file_bytes, stem))
    return rows


def group_by_student(rows: list) -> dict:
    """页记录 → {student_id: {name, class_code, pages:{page_no: image}, complete}}"""
    students = {}
    for r in rows:
        s = students.setdefault(r["student_id"], {
            "student_name": r["student_name"],
            "class_code": r["class_code"],
            "total_pages": r["total_pages"],
            "pages": {},
        })
        s["pages"][r["page_no"]] = r["image"]
    for s in students.values():
        s["complete"] = len(s["pages"]) == s["total_pages"]
    return students


# ════════════════════════════════════════════════════════════
# 中位底图：同一页取全班像素中位数 = 干净题目背景
# 替代「空白卷差分」——Classkick 空白卷走另一条导出管线，像素对不齐
# ════════════════════════════════════════════════════════════

def median_background(images: list, sample: int = 15) -> Image.Image:
    """从同一页的多张学生图估计干净底图。少于 MIN_STUDENTS_FOR_BG 张返回 None。"""
    if len(images) < MIN_STUDENTS_FOR_BG:
        return None
    size = images[0].size
    usable = [im for im in images if im.size == size]
    if len(usable) < MIN_STUDENTS_FOR_BG:
        return None            # 分辨率不齐，放弃（不猜、不强行缩放）
    step = max(1, len(usable) // sample)
    picked = usable[::step][:sample]
    stack = np.stack([np.asarray(im, dtype=np.uint8) for im in picked], axis=0)
    return Image.fromarray(np.median(stack, axis=0).astype(np.uint8))


def ink_layer(student_img: Image.Image, bg_img: Image.Image, threshold: int = 40):
    """学生页 − 底图 = 纯笔迹（白底黑字）。返回 (笔迹图, 墨迹像素占比)。"""
    if bg_img is None or student_img.size != bg_img.size:
        return None, None
    a = np.asarray(student_img, dtype=np.int16)
    b = np.asarray(bg_img, dtype=np.int16)
    diff = np.abs(a - b).max(axis=2)
    mask = diff > threshold
    ink = np.full(a.shape[:2], 255, dtype=np.uint8)
    ink[mask] = np.asarray(student_img.convert("L"), dtype=np.uint8)[mask]
    return Image.fromarray(ink, mode="L"), float(mask.mean())


def has_answer(ink_ratio, min_ratio: float = 0.0008) -> bool:
    """墨迹占比低于阈值 = 这一页没作答。ratio 为 None 时保守认为写了。"""
    return True if ink_ratio is None else ink_ratio >= min_ratio


def prepare_for_ocr(img: Image.Image, min_width: int = MIN_WIDTH_FOR_OCR,
                    quality: int = 92) -> bytes:
    """分辨率不足先放大，再转 JPEG。Classkick 导出分辨率不固定，一律实测。"""
    if img.width < min_width:
        scale = min_width / img.width
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()
