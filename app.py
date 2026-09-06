#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Courseware Upload Portal  --  zero-dependency (Python standard library only)

Run:
    python app.py
Then open http://localhost:8000  (or set PORT env to change)

Routes:
    GET  /            -> upload form (name + student id + note + files)
    POST /upload      -> receive multipart, save files + metadata
    GET  /gallery     -> public wall listing all submissions (download links)
    GET  /download/<id> -> download a stored file
    GET  /delete/<id>?admin=<TOKEN> -> delete a submission (teacher only)
    GET  /materials   -> course materials page (课件下载 / 教学大纲 / 教材参考书)
    GET  /mat/<slug>  -> serve a course-material file (whitelist from JSON)
    GET  /materials-admin -> teacher edit window (upload / edit / delete materials)
    POST /materials-admin -> handle admin edits (requires admin token)

All user-supplied text is HTML-escaped before rendering (no XSS).
"""

import os
import re
import io
import sys
import json
import html
import sqlite3
import uuid
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ----------------------------- CONFIG -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
DB_PATH = os.path.join(BASE_DIR, "submissions.db")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "change-me-2026")
PORT = int(os.environ.get("PORT", "8000"))
HOST = os.environ.get("HOST", "0.0.0.0")
MAX_SIZE = int(os.environ.get("MAX_SIZE", str(200 * 1024 * 1024)))  # 200 MB
ALLOWED_EXT = set()  # empty = allow all; fill to restrict, e.g. {'.pdf','.ppt','.pptx'}

os.makedirs(UPLOAD_DIR, exist_ok=True)

# ----------------------------- COURSE MATERIALS -----------------------------
SLIDES_DIR = os.path.join(BASE_DIR, "slides")   # compiled English Beamer PDFs
PPT_DIR = os.path.join(BASE_DIR, "ppt")          # English source slides (.pptx)
MATERIALS_UPLOAD_DIR = os.path.join(BASE_DIR, "materials_uploads")  # teacher uploads
MATERIALS_PATH = os.path.join(BASE_DIR, "materials_data.json")
os.makedirs(MATERIALS_UPLOAD_DIR, exist_ok=True)

# Default content (used only if materials_data.json is missing).
DEFAULT_MATERIALS = {
    "lectures": [
        {"slug": "ch1", "file": "slides/ch1_intro_beamer.pdf",
         "title_zh": "第1章 · 引言 + 映射与函数",
         "title_en": "Chapter 1 — Introduction, Mappings and Functions", "pages": "137"},
        {"slug": "ch2", "file": "slides/ch2_limits_beamer.pdf",
         "title_zh": "第2章 · 数列的极限 + 函数的极限",
         "title_en": "Chapter 2 — Limits of Sequences and Functions", "pages": "151"},
        {"slug": "ch2c", "file": "slides/ch2_limits_cont_beamer.pdf",
         "title_zh": "第2章续 · 无穷小与无穷大 + 极限运算法则 + 无穷小比较",
         "title_en": "Chapter 2 (cont.) — Infinitesimals, Limit Laws, Order of Infinitesimals",
         "pages": "141"},
    ],
    "sections": [
        {"slug": "S1_1", "section": "§1.1", "title_zh": "映射与函数",
         "title_en": "Mappings and Functions", "file": "ppt/S1_1映射与函数_en.pptx"},
        {"slug": "S1_2", "section": "§1.2", "title_zh": "数列的极限",
         "title_en": "Limits of Sequences", "file": "ppt/S1_2数列的极限_en.pptx"},
        {"slug": "S1_3", "section": "§1.3", "title_zh": "函数的极限",
         "title_en": "Limits of Functions", "file": "ppt/S1_3函数的极限_en.pptx"},
        {"slug": "S1_4", "section": "§1.4", "title_zh": "无穷小与无穷大",
         "title_en": "Infinitesimals and Infinities", "file": "ppt/S1_4无穷小与无穷大_en.pptx"},
        {"slug": "S1_5", "section": "§1.5", "title_zh": "极限运算法则",
         "title_en": "Limit Laws", "file": "ppt/S1_5极限运算法则_en.pptx"},
        {"slug": "S1_6", "section": "§1.6", "title_zh": "极限存在准则",
         "title_en": "Criteria for Existence of Limits", "file": "ppt/S1_6极限存在准则_en.pptx"},
        {"slug": "S1_7", "section": "§1.7", "title_zh": "无穷小的比较",
         "title_en": "Comparison of Infinitesimals", "file": "ppt/S1_7无穷小比较_en.pptx"},
        {"slug": "S1_8", "section": "§1.8", "title_zh": "连续性与间断点",
         "title_en": "Continuity and Discontinuities", "file": "ppt/S1_8连续性间断点_en.pptx"},
        {"slug": "S1_9", "section": "§1.9", "title_zh": "连续函数的运算",
         "title_en": "Operations on Continuous Functions", "file": "ppt/S1_9连续函数运算_en.pptx"},
        {"slug": "S1_10", "section": "§1.10", "title_zh": "连续函数的性质",
         "title_en": "Properties of Continuous Functions", "file": "ppt/S1_10连续函数性质_en.pptx"},
    ],
    "syllabus": [
        {"chapter": "第1章", "zh": "函数与极限", "en": "Functions and Limits",
         "hours": "约 18 学时", "term": "第一学期", "status": "ready"},
        {"chapter": "第2章", "zh": "导数与微分", "en": "Derivatives and Differentials",
         "hours": "约 16 学时", "term": "第一学期", "status": "todo"},
        {"chapter": "第3章", "zh": "微分中值定理与导数的应用",
         "en": "Mean Value Theorems and Applications", "hours": "约 14 学时",
         "term": "第一学期", "status": "todo"},
        {"chapter": "第4章", "zh": "不定积分", "en": "Indefinite Integrals",
         "hours": "约 12 学时", "term": "第一学期", "status": "todo"},
        {"chapter": "第5章", "zh": "定积分", "en": "Definite Integrals",
         "hours": "约 12 学时", "term": "第一学期", "status": "todo"},
        {"chapter": "第6章", "zh": "定积分的应用", "en": "Applications of Definite Integrals",
         "hours": "约 10 学时", "term": "第二学期", "status": "todo"},
        {"chapter": "第7章", "zh": "微分方程", "en": "Differential Equations",
         "hours": "约 14 学时", "term": "第二学期", "status": "todo"},
    ],
    "textbooks": [
        {"kind": "主教材", "title": "《高等数学》第八版（上册 / 下册）",
         "note": "同济大学数学科学学院 编，高等教育出版社"},
        {"kind": "习题指导", "title": "《高等数学习题全解指导（配第八版）》",
         "note": "上册 / 下册，与主教材配套"},
        {"kind": "竞赛参考", "title": "《全国大学生数学竞赛解析教程（非数学专业类）》",
         "note": "余志坤 主编，科学出版社，2023"},
    ],
}


def load_materials():
    """Load course-material content from materials_data.json.

    Falls back to DEFAULT_MATERIALS if the file is missing or corrupt.
    """
    try:
        with open(MATERIALS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {k: list(v) for k, v in DEFAULT_MATERIALS.items()}
    for k in ("lectures", "sections", "syllabus", "textbooks"):
        data.setdefault(k, [])
    return data


def save_materials(data):
    """Persist content to disk, then best-effort push to git (Render durable)."""
    tmp = MATERIALS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MATERIALS_PATH)
    _git_persist()


def _git_persist():
    """Best-effort commit of material changes so they survive a Render
    restart (free plan has ephemeral disk). Requires GH_PUSH_TOKEN env var.
    Never raises — failures are silently ignored so saving still works.
    """
    token = os.environ.get("GH_PUSH_TOKEN")
    if not token:
        return
    try:
        import subprocess
        repo = subprocess.check_output(
            ["git", "-C", BASE_DIR, "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL).decode().strip()
        if not repo.startswith("https://"):
            return
        push_url = re.sub(r"https://([^@]+@)?", "https://" + token + "@", repo)
        subprocess.run(["git", "-C", BASE_DIR, "add", "materials_data.json",
                        "materials_uploads"], check=False, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "-C", BASE_DIR, "commit", "-m",
                        "update course materials (admin)"],
                       check=False, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "-C", BASE_DIR, "push", push_url, "HEAD:main"],
                       check=False, stderr=subprocess.DEVNULL, timeout=60)
    except Exception:
        pass


# ----------------------------- DATABASE -----------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            student_id TEXT,
            note TEXT,
            filename TEXT,
            stored_name TEXT,
            size INTEGER,
            ts TEXT
        )"""
    )
    conn.commit()
    conn.close()


def now_str():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------- MULTIPART PARSER -----------------------------
def parse_multipart(body, boundary):
    """Minimal multipart/form-data parser (cgi module removed in 3.13)."""
    parts = []
    delim = b"--" + boundary
    segments = body.split(delim)
    for seg in segments:
        if seg in (b"", b"--", b"--\r\n"):
            continue
        if seg.startswith(b"--"):  # closing boundary
            continue
        if seg.startswith(b"\r\n"):
            seg = seg[2:]
        if seg.endswith(b"\r\n"):
            seg = seg[:-2]
        if b"\r\n\r\n" not in seg:
            continue
        head, content = seg.split(b"\r\n\r\n", 1)
        headers = {}
        for line in head.split(b"\r\n"):
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.decode().strip().lower()] = v.decode().strip()
        cd = headers.get("content-disposition", "")
        name = None
        filename = None
        m = re.search(r'name="([^"]*)"', cd)
        if m:
            name = m.group(1)
        m = re.search(r'filename="([^"]*)"', cd)
        if m:
            filename = m.group(1)
        parts.append({"name": name, "filename": filename, "content": content})
    return parts


def sanitize_filename(name):
    name = os.path.basename(name or "file")
    name = name.replace("\x00", "")
    # keep unicode but strip path separators / control chars
    name = re.sub(r'[\\/:"*?<>|]', "_", name)
    if not name:
        name = "file"
    return name


# ----------------------------- HTML TEMPLATES -----------------------------
PAGE_CSS = """
:root{
  --bg:#f4f7fb; --card:#ffffff; --primary:#2563eb; --primary2:#1e40af;
  --ink:#1f2937; --muted:#6b7280; --line:#e5e7eb; --ok:#16a34a;
}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
  background:var(--bg);color:var(--ink);line-height:1.6}
header{background:linear-gradient(120deg,var(--primary),var(--primary2));color:#fff;padding:30px 20px;text-align:center}
header h1{margin:0;font-size:24px;letter-spacing:.5px}
header p{margin:6px 0 0;opacity:.9;font-size:14px}
.wrap{max-width:860px;margin:24px auto;padding:0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px;box-shadow:0 4px 18px rgba(30,64,175,.06)}
label{display:block;font-weight:600;margin:14px 0 6px;font-size:14px}
input[type=text],textarea{width:100%;padding:11px 12px;border:1px solid var(--line);border-radius:9px;font-size:14px;font-family:inherit}
textarea{resize:vertical;min-height:64px}
.drop{margin-top:14px;border:2px dashed #c7d2fe;background:#f8faff;border-radius:12px;padding:22px;text-align:center;color:var(--muted);cursor:pointer;transition:.15s}
.drop.over{border-color:var(--primary);background:#eef4ff;color:var(--primary)}
.drop input{display:none}
#filelist{margin-top:10px;font-size:13px;color:var(--muted)}
#filelist div{padding:4px 0;border-bottom:1px dashed var(--line)}
button{margin-top:18px;background:var(--primary);color:#fff;border:0;border-radius:10px;padding:12px 22px;font-size:15px;font-weight:600;cursor:pointer}
button:hover{background:var(--primary2)}
button:disabled{opacity:.6;cursor:not-allowed}
.note{font-size:12px;color:var(--muted);margin-top:8px}
.banner{background:#ecfdf5;color:#065f46;border:1px solid #a7f3d0;padding:12px 14px;border-radius:10px;margin-bottom:16px;font-size:14px}
table{width:100%;border-collapse:collapse;margin-top:6px}
th,td{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);font-size:14px;vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.4px}
a.dl{color:var(--primary);text-decoration:none;font-weight:600}
a.dl:hover{text-decoration:underline}
.search{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:9px;font-size:14px;margin-bottom:12px}
.empty{color:var(--muted);text-align:center;padding:30px}
.tag{display:inline-block;background:#eef2ff;color:#3730a3;border-radius:6px;padding:1px 8px;font-size:12px;margin-right:4px}
.del{color:#dc2626;text-decoration:none;font-size:13px}
footer{text-align:center;color:var(--muted);font-size:12px;padding:24px}
.topnav{text-align:right;max-width:860px;margin:14px auto 0;padding:0 16px}
.topnav a{color:var(--primary);text-decoration:none;font-size:13px;font-weight:600}
h2.sec{font-size:18px;margin:0 0 12px;padding-left:10px;border-left:4px solid var(--primary)}
h2.sec .en{font-size:13px;color:var(--muted);font-weight:400;margin-left:6px}
h3.sub{font-size:14px;margin:22px 0 6px;color:var(--primary2)}
.en-sm{font-size:12px;color:var(--muted);font-weight:400}
.badge{display:inline-block;background:#f3f4f6;color:#6b7280;border-radius:6px;padding:1px 8px;font-size:12px}
.badge.ok{background:#ecfdf5;color:#047857}
.badge.term1{background:#eff6ff;color:#1d4ed8}
.badge.term2{background:#fff7ed;color:#c2410c}
"""

NAV = (
    '<a href="/">＋ 上传</a> &nbsp;·&nbsp; '
    '<a href="/gallery">作品墙</a> &nbsp;·&nbsp; '
    '<a href="/materials">课堂资料</a> &nbsp;·&nbsp; '
    '<a href="/materials-admin">管理资料</a>'
)

UPLOAD_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>课件上传</title><style>{css}</style></head>
<body>
<header><h1>课件上传</h1><p>填写信息并上传文件，提交后会出现在作品墙</p></header>
<div class="topnav">{nav}</div>
<div class="wrap">
{card}
</div>
<footer>Courseware Upload Portal</footer>
<script>
var dz=document.getElementById('drop');
var inp=document.getElementById('f');
var fl=document.getElementById('filelist');
dz.onclick=function(){inp.click();};
dz.ondragover=function(e){e.preventDefault();dz.classList.add('over');};
dz.ondragleave=function(){dz.classList.remove('over');};
dz.ondrop=function(e){e.preventDefault();dz.classList.remove('over');inp.files=e.dataTransfer.files;show();};
inp.onchange=show;
function show(){fl.innerHTML='';[].forEach.call(inp.files,function(f){var d=document.createElement('div');d.textContent='• '+f.name+'  ('+(f.size/1024).toFixed(1)+' KB)';fl.appendChild(d);});}
var form=document.getElementById('form');
form.onsubmit=function(){document.getElementById('btn').disabled=true;document.getElementById('btn').textContent='上传中…';};
</script>
</body></html>"""

UPLOAD_CARD = """<div class="card">
{banner}
<form id="form" method="post" action="/upload" enctype="multipart/form-data">
  <label for="name">姓名</label>
  <input type="text" id="name" name="name" placeholder="请输入姓名" required>
  <label for="sid">学号</label>
  <input type="text" id="sid" name="student_id" placeholder="请输入学号" required>
  <label for="note">备注（可选）</label>
  <textarea id="note" name="note" placeholder="例如：第3章作业 / 课件说明"></textarea>
  <label>文件</label>
  <div class="drop" id="drop">点击选择，或把文件拖到这里（可多选）<input id="f" type="file" name="file" multiple required></div>
  <div id="filelist"></div>
  <button id="btn" type="submit">提交上传</button>
  <div class="note">支持 ppt / pptx / pdf / doc / docx / 图片 / 压缩包等；单个文件上限 200 MB。</div>
</form>
</div>"""

GALLERY_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>作品墙</title><style>{css}</style></head>
<body>
<header><h1>课件作品墙</h1><p>所有提交在这里公开展示，可点击下载</p></header>
<div class="topnav">{nav}</div>
<div class="wrap">
<div class="card">
  <input class="search" id="q" placeholder="搜索姓名 / 学号 / 备注…">
  {rows}
</div>
</div>
<footer>Courseware Upload Portal</footer>
<script>
var q=document.getElementById('q');
var rows=document.querySelectorAll('tr[data-t]');
q.oninput=function(){var v=q.value.trim().toLowerCase();rows.forEach(function(r){r.style.display=r.getAttribute('data-t').toLowerCase().indexOf(v)>=0?'':'none';});};
</script>
</body></html>"""


MATERIALS_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>课堂资料</title><style>{css}</style></head>
<body>
<header><h1>课堂资料</h1><p>高等数学（同济 第八版）· Course Materials</p></header>
<div class="topnav">{nav}</div>
<div class="wrap">
  <div class="card">
    <h2 class="sec">一、课件下载 <span class="en">Course Files</span></h2>
    <h3 class="sub">英文讲义 PDF（可在线预览 / 下载）</h3>
    {lectures}
    <h3 class="sub">分节课件 PPT（源文件，可编辑）</h3>
    {sections}
  </div>
  <div class="card" style="margin-top:18px">
    <h2 class="sec">二、课程大纲与教学进度 <span class="en">Syllabus</span></h2>
    {syllabus}
    <div class="note">学时为参考值，可按实际教学安排调整；第1章课件已上线，其余章节将陆续更新。</div>
  </div>
  <div class="card" style="margin-top:18px">
    <h2 class="sec">三、教材与参考书 <span class="en">Textbooks</span></h2>
    {books}
  </div>
</div>
<footer>Courseware Portal · 课堂资料</footer>
</body></html>"""


def _size_str(path):
    try:
        n = os.path.getsize(path)
    except OSError:
        return "—"
    if n >= 1024 * 1024:
        return f"{n/1024/1024:.1f} MB"
    return f"{n/1024:.0f} KB"


def materials_page():
    M = load_materials()

    # --- lecture PDFs ---
    rows = []
    for it in M["lectures"]:
        p = os.path.join(BASE_DIR, it.get("file", ""))
        ok = os.path.isfile(p)
        link = (f"<a class='dl' href='/mat/{html.escape(it['slug'])}'>下载 / 预览</a>"
                if ok else "<span class='note'>文件缺失</span>")
        rows.append(
            f"<tr><td>{html.escape(it.get('title_zh',''))}"
            f"<div class='en-sm'>{html.escape(it.get('title_en',''))}</div></td>"
            f"<td>{html.escape(str(it.get('pages','')))} 页</td>"
            f"<td>{_size_str(p)}</td><td>{link}</td></tr>")
    lectures = ("<table><thead><tr><th>内容</th><th>页数</th><th>大小</th>"
                "<th>下载</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")

    # --- section PPTs ---
    rows = []
    for it in M["sections"]:
        p = os.path.join(BASE_DIR, it.get("file", ""))
        ok = os.path.isfile(p)
        link = (f"<a class='dl' href='/mat/{html.escape(it['slug'])}'>PPT</a>"
                if ok else "<span class='note'>缺失</span>")
        rows.append(
            f"<tr><td><span class='tag'>{html.escape(it.get('section',''))}</span></td>"
            f"<td>{html.escape(it.get('title_zh',''))}</td>"
            f"<td class='en-sm'>{html.escape(it.get('title_en',''))}</td>"
            f"<td>{_size_str(p)}</td><td>{link}</td></tr>")
    sections = ("<table><thead><tr><th>小节</th><th>内容</th><th>English</th>"
                "<th>大小</th><th>下载</th></tr></thead><tbody>"
                + "".join(rows) + "</tbody></table>")

    # --- syllabus ---
    rows = []
    for it in M["syllabus"]:
        badge = ("<span class='badge ok'>课件已上线</span>"
                 if it.get("status") == "ready" else "<span class='badge'>待更新</span>")
        term = it.get("term", "")
        term_cls = "badge term1" if term == "第一学期" else ("badge term2" if term == "第二学期" else "badge")
        term_html = f"<span class='{term_cls}'>{html.escape(term)}</span>" if term else ""
        rows.append(
            f"<tr><td><b>{html.escape(it.get('chapter',''))}</b></td>"
            f"<td>{html.escape(it.get('zh',''))}"
            f"<div class='en-sm'>{html.escape(it.get('en',''))}</div></td>"
            f"<td>{html.escape(it.get('hours',''))}</td>"
            f"<td>{term_html}</td><td>{badge}</td></tr>")
    syllabus = ("<table><thead><tr><th>章节</th><th>内容</th><th>学时</th>"
                "<th>学期</th><th>课件</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")

    # --- textbooks ---
    rows = []
    for it in M["textbooks"]:
        rows.append(
            f"<tr><td><span class='tag'>{html.escape(it.get('kind',''))}</span></td>"
            f"<td>{html.escape(it.get('title',''))}"
            f"<div class='en-sm'>{html.escape(it.get('note',''))}</div></td></tr>")
    books = ("<table><thead><tr><th>类别</th><th>书目</th></tr></thead><tbody>"
             + "".join(rows) + "</tbody></table>")

    return (MATERIALS_PAGE
            .replace("{css}", PAGE_CSS)
            .replace("{nav}", NAV)
            .replace("{lectures}", lectures)
            .replace("{sections}", sections)
            .replace("{syllabus}", syllabus)
            .replace("{books}", books))


# ----------------------------- ADMIN (self-service) -----------------------------
ADMIN_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>资料管理</title><style>{css}</style></head>
<body>
<header><h1>课堂资料管理</h1><p>上传新资料 · 修改标题 / 大纲 / 教材（无需找开发者）</p></header>
<div class="topnav">{nav}</div>
<div class="wrap">
{body}
</div>
<footer>Courseware Portal · 资料管理</footer>
</body></html>"""


def _new_slug(stem, existing):
    base = re.sub(r'[^A-Za-z0-9_]+', '_', stem).strip('_') or "m"
    s = base
    i = 2
    while s in existing:
        s = base + "_" + str(i)
        i += 1
    return s


def admin_login_page(wrong=False):
    msg = ('<div class="banner" style="background:#fef2f2;color:#991b1b;'
           'border-color:#fecaca">口令错误，请重试。</div>') if wrong else ""
    body = msg + """<div class="card">
  <h2 class="sec">管理员登录</h2>
  <form method="get" action="/materials-admin">
    <label>管理员口令</label>
    <input type="password" name="admin" required>
    <button type="submit">进入管理</button>
  </form>
  <div class="note">口令与作品墙删除用的相同（ADMIN_TOKEN）。</div>
</div>"""
    return (ADMIN_PAGE.replace("{css}", PAGE_CSS)
            .replace("{nav}", NAV).replace("{body}", body))


def admin_dashboard(token, edit=None):
    M = load_materials()
    b = []
    b.append('<div class="note" style="margin-bottom:14px">编辑会自动保存；'
             '在免费版 Render 上会通过 Git 自动存回仓库，重启也不丢。</div>')
    if edit:
        ef = _edit_form(M, edit, token)
        if ef:
            b.append(ef)
    # upload
    b.append(
        '<div class="card"><h2 class="sec">上传新资料 <span class="en">Add a file</span></h2>'
        '<form method="post" action="/materials-admin" enctype="multipart/form-data">'
        '<input type="hidden" name="admin" value="' + html.escape(token) + '">'
        '<input type="hidden" name="action" value="upload">'
        '<label>类型</label><select name="kind"><option value="lecture">英文讲义 PDF</option>'
        '<option value="section">分节 PPT</option></select>'
        '<label>小节标识（仅 PPT，如 §1.11）</label>'
        '<input type="text" name="section" placeholder="§1.11">'
        '<label>中文标题 *</label><input type="text" name="title_zh" required>'
        '<label>English 标题</label><input type="text" name="title_en">'
        '<label>页数（仅 PDF，可选）</label><input type="text" name="pages" placeholder="如 88">'
        '<label>文件（PDF / PPT / PPTX）*</label><input type="file" name="file" required>'
        '<button type="submit">保存并上传</button></form></div>')
    # lectures
    b.append('<div class="card" style="margin-top:18px"><h2 class="sec">讲义 PDF 管理</h2>'
             '<table><thead><tr><th>中文标题</th><th>English</th><th>页数</th>'
             '<th>文件</th><th></th></tr></thead><tbody>')
    for it in M["lectures"]:
        s = it.get("slug", "")
        b.append("<tr><td>" + html.escape(it.get("title_zh", "")) + "</td>"
                 "<td class='en-sm'>" + html.escape(it.get("title_en", "")) + "</td>"
                 "<td>" + html.escape(str(it.get("pages", ""))) + "</td>"
                 "<td class='en-sm'>" + html.escape(it.get("file", "")) + "</td>"
                 "<td><a class='dl' href='/materials-admin?admin=" + urllib.parse.quote(token)
                 + "&amp;edit=lecture:" + urllib.parse.quote(s) + "'>编辑</a> "
                 "<a class='del' href='/materials-admin?admin=" + urllib.parse.quote(token)
                 + "&amp;action=delete&amp;kind=lecture&amp;slug=" + urllib.parse.quote(s)
                 + "' onclick=\"return confirm('确认删除？')\">删除</a></td></tr>")
    b.append('</tbody></table></div>')
    # sections
    b.append('<div class="card" style="margin-top:18px"><h2 class="sec">分节 PPT 管理</h2>'
             '<table><thead><tr><th>小节</th><th>中文标题</th><th>English</th>'
             '<th>文件</th><th></th></tr></thead><tbody>')
    for it in M["sections"]:
        s = it.get("slug", "")
        b.append("<tr><td><span class='tag'>" + html.escape(it.get("section", ""))
                 + "</span></td><td>" + html.escape(it.get("title_zh", ""))
                 + "</td><td class='en-sm'>" + html.escape(it.get("title_en", ""))
                 + "</td><td class='en-sm'>" + html.escape(it.get("file", ""))
                 + "</td><td><a class='dl' href='/materials-admin?admin=" + urllib.parse.quote(token)
                 + "&amp;edit=section:" + urllib.parse.quote(s) + "'>编辑</a> "
                 "<a class='del' href='/materials-admin?admin=" + urllib.parse.quote(token)
                 + "&amp;action=delete&amp;kind=section&amp;slug=" + urllib.parse.quote(s)
                 + "' onclick=\"return confirm('确认删除？')\">删除</a></td></tr>")
    b.append('</tbody></table></div>')
    # syllabus
    b.append('<div class="card" style="margin-top:18px"><h2 class="sec">课程大纲与教学进度</h2>'
             '<form method="post" action="/materials-admin" style="margin-bottom:14px">'
             '<input type="hidden" name="admin" value="' + html.escape(token) + '">'
             '<input type="hidden" name="action" value="add_syllabus">'
             '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:end">'
             '<span><label>章节</label><input type="text" name="chapter" placeholder="第8章" required></span>'
             '<span><label>中文</label><input type="text" name="zh" placeholder="空间解析几何" required></span>'
             '<span><label>English</label><input type="text" name="en" placeholder="..."></span>'
             '<span><label>学时</label><input type="text" name="hours" placeholder="约 10 学时"></span>'
             '<span><label>学期</label><select name="term"><option value="">—</option>'
             '<option value="第一学期">第一学期</option>'
             '<option value="第二学期">第二学期</option></select></span>'
             '<span><label>状态</label><select name="status"><option value="todo">待更新</option>'
             '<option value="ready">课件已上线</option></select></span>'
             '<button type="submit">＋ 添加章节</button></div></form>'
             '<table><thead><tr><th>章节</th><th>中文</th><th>English</th><th>学时</th>'
             '<th>学期</th><th>状态</th><th></th></tr></thead><tbody>')
    for i, it in enumerate(M["syllabus"]):
        b.append("<tr><td><b>" + html.escape(it.get("chapter", "")) + "</b></td>"
                 "<td>" + html.escape(it.get("zh", "")) + "</td>"
                 "<td class='en-sm'>" + html.escape(it.get("en", "")) + "</td>"
                 "<td>" + html.escape(it.get("hours", "")) + "</td>"
                 "<td>" + html.escape(it.get("term", "")) + "</td>"
                 "<td>" + html.escape(it.get("status", "")) + "</td>"
                 "<td><a class='dl' href='/materials-admin?admin=" + urllib.parse.quote(token)
                 + "&amp;edit=syllabus:" + str(i) + "'>编辑</a> "
                 "<a class='del' href='/materials-admin?admin=" + urllib.parse.quote(token)
                 + "&amp;action=delete&amp;kind=syllabus&amp;idx=" + str(i)
                 + "' onclick=\"return confirm('确认删除？')\">删除</a></td></tr>")
    b.append('</tbody></table></div>')
    # textbooks
    b.append('<div class="card" style="margin-top:18px"><h2 class="sec">教材与参考书</h2>'
             '<form method="post" action="/materials-admin" style="margin-bottom:14px">'
             '<input type="hidden" name="admin" value="' + html.escape(token) + '">'
             '<input type="hidden" name="action" value="add_textbook">'
             '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:end">'
             '<span><label>类别</label><input type="text" name="kind" placeholder="主教材" required></span>'
             '<span><label>书名</label><input type="text" name="title" placeholder="《...》" required></span>'
             '<span><label>说明</label><input type="text" name="note" placeholder="出版社 / 作者"></span>'
             '<button type="submit">＋ 添加书目</button></div></form>'
             '<table><thead><tr><th>类别</th><th>书名</th><th>说明</th><th></th></tr></thead><tbody>')
    for i, it in enumerate(M["textbooks"]):
        b.append("<tr><td><span class='tag'>" + html.escape(it.get("kind", ""))
                 + "</span></td><td>" + html.escape(it.get("title", ""))
                 + "</td><td class='en-sm'>" + html.escape(it.get("note", ""))
                 + "</td><td><a class='dl' href='/materials-admin?admin=" + urllib.parse.quote(token)
                 + "&amp;edit=textbook:" + str(i) + "'>编辑</a> "
                 "<a class='del' href='/materials-admin?admin=" + urllib.parse.quote(token)
                 + "&amp;action=delete&amp;kind=textbook&amp;idx=" + str(i)
                 + "' onclick=\"return confirm('确认删除？')\">删除</a></td></tr>")
    b.append('</tbody></table></div>')
    return (ADMIN_PAGE.replace("{css}", PAGE_CSS)
            .replace("{nav}", NAV).replace("{body}", "\n".join(b)))


def _edit_form(M, edit, token):
    t = html.escape(token)
    if ":" not in edit:
        return None
    kind, key = edit.split(":", 1)
    if kind in ("lecture", "section"):
        item = next((x for x in M[kind + "s"] if x.get("slug") == key), None)
        if not item:
            return None
        is_lec = kind == "lecture"
        sec = ("<label>小节标识</label><input type='text' name='section' value='"
               + html.escape(item.get("section", "")) + "'>") if not is_lec else ""
        pages = ("<label>页数</label><input type='text' name='pages' value='"
                 + html.escape(str(item.get("pages", ""))) + "'>") if is_lec else ""
        return ('<div class="card" style="border-color:#2563eb"><h2 class="sec">编辑 '
                + ("讲义" if is_lec else "分节PPT") + "：" + html.escape(item.get("title_zh", ""))
                + '</h2><form method="post" action="/materials-admin" enctype="multipart/form-data">'
                '<input type="hidden" name="admin" value="' + t + '">'
                '<input type="hidden" name="action" value="edit">'
                '<input type="hidden" name="kind" value="' + kind + '">'
                '<input type="hidden" name="slug" value="' + html.escape(key) + '">'
                + sec
                + "<label>中文标题 *</label><input type='text' name='title_zh' value='"
                + html.escape(item.get("title_zh", "")) + "' required>"
                + "<label>English 标题</label><input type='text' name='title_en' value='"
                + html.escape(item.get("title_en", "")) + "'>"
                + pages
                + "<label>替换文件（可选，留空则保留原文件）</label><input type='file' name='file'>"
                "<button type='submit'>保存修改</button> "
                "<a class='del' href='/materials-admin?admin=" + urllib.parse.quote(token)
                + "' style='margin-left:12px'>取消</a></form></div>")
    elif kind in ("syllabus", "textbook"):
        if not key.isdigit():
            return None
        idx = int(key)
        lst = M[kind]
        if idx < 0 or idx >= len(lst):
            return None
        item = lst[idx]
        if kind == "syllabus":
            sel_todo = " selected" if item.get("status") != "ready" else ""
            sel_ready = " selected" if item.get("status") == "ready" else ""
            return ('<div class="card" style="border-color:#2563eb"><h2 class="sec">编辑章节</h2>'
                    '<form method="post" action="/materials-admin">'
                    '<input type="hidden" name="admin" value="' + t + '">'
                    '<input type="hidden" name="action" value="edit">'
                    '<input type="hidden" name="kind" value="syllabus">'
                    '<input type="hidden" name="idx" value="' + str(idx) + '">'
                    "<label>章节 *</label><input type='text' name='chapter' value='"
                    + html.escape(item.get("chapter", "")) + "' required>"
                    "<label>中文 *</label><input type='text' name='zh' value='"
                    + html.escape(item.get("zh", "")) + "' required>"
                    "<label>English</label><input type='text' name='en' value='"
                    + html.escape(item.get("en", "")) + "'>"
                    "<label>学时</label><input type='text' name='hours' value='"
                    + html.escape(item.get("hours", "")) + "'>"
                    "<label>学期</label><select name='term'>"
                    + "<option value=''{sel1}>—</option>".replace("{sel1}",
                        " selected" if item.get("term") not in ("第一学期", "第二学期") else "")
                    + "<option value='第一学期'{s1}>第一学期</option>".replace("{s1}",
                        " selected" if item.get("term") == "第一学期" else "")
                    + "<option value='第二学期'{s2}>第二学期</option>".replace("{s2}",
                        " selected" if item.get("term") == "第二学期" else "")
                    + "</select>"
                    "<label>状态</label><select name='status'>"
                    "<option value='todo'" + sel_todo + ">待更新</option>"
                    "<option value='ready'" + sel_ready + ">课件已上线</option></select>"
                    "<button type='submit'>保存修改</button> "
                    "<a class='del' href='/materials-admin?admin=" + urllib.parse.quote(token)
                    + "' style='margin-left:12px'>取消</a></form></div>")
        else:
            return ('<div class="card" style="border-color:#2563eb"><h2 class="sec">编辑书目</h2>'
                    '<form method="post" action="/materials-admin">'
                    '<input type="hidden" name="admin" value="' + t + '">'
                    '<input type="hidden" name="action" value="edit">'
                    '<input type="hidden" name="kind" value="textbook">'
                    '<input type="hidden" name="idx" value="' + str(idx) + '">'
                    "<label>类别 *</label><input type='text' name='kind_v' value='"
                    + html.escape(item.get("kind", "")) + "' required>"
                    "<label>书名 *</label><input type='text' name='title' value='"
                    + html.escape(item.get("title", "")) + "' required>"
                    "<label>说明</label><input type='text' name='note' value='"
                    + html.escape(item.get("note", "")) + "'>"
                    "<button type='submit'>保存修改</button> "
                    "<a class='del' href='/materials-admin?admin=" + urllib.parse.quote(token)
                    + "' style='margin-left:12px'>取消</a></form></div>")
    return None


def gallery_rows(admin=False):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id,name,student_id,note,filename,size,ts FROM submissions ORDER BY id DESC"
    ).fetchall()
    conn.close()
    if not rows:
        return '<div class="empty">还没有任何提交。</div>'
    out = ["<table><thead><tr><th>时间</th><th>姓名</th><th>学号</th><th>备注</th><th>文件</th><th></th></tr></thead><tbody>"]
    for r in rows:
        rid, name, sid, note, fname, size, ts = r
        name = html.escape(name or "")
        sid = html.escape(sid or "")
        note = html.escape(note or "")
        fname = html.escape(fname or "")
        size_kb = f"{(size or 0)/1024:.1f} KB"
        data_t = f"{name} {sid} {note}"
        del_link = ""
        if admin:
            del_link = f'<a class="del" href="/delete/{rid}?admin={ADMIN_TOKEN}" onclick="return confirm(\'确认删除？\')">删除</a>'
        out.append(
            f"<tr data-t=\"{html.escape(data_t)}\">"
            f"<td>{html.escape(ts)}</td>"
            f"<td><span class='tag'>{name}</span></td>"
            f"<td>{sid}</td>"
            f"<td>{note}</td>"
            f"<td><a class='dl' href='/download/{rid}'>{fname}</a> <span class='note'>{size_kb}</span></td>"
            f"<td>{del_link}</td></tr>"
        )
    out.append("</tbody></table>")
    return "\n".join(out)


# ----------------------------- HANDLER -----------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # quiet

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/" or path == "/index.html":
            banner = ""
            if parsed.query == "ok=1":
                banner = '<div class="banner">✅ 上传成功！可在 <a href="/gallery" style="color:#065f46;font-weight:700">作品墙</a> 查看。</div>'
            card = UPLOAD_CARD.replace("{banner}", banner)
            self._send(
                200,
                UPLOAD_PAGE.replace("{css}", PAGE_CSS)
                .replace("{card}", card)
                .replace("{nav}", NAV),
            )
        elif path == "/gallery":
            admin = urllib.parse.parse_qs(parsed.query).get("admin", [""])[0] == ADMIN_TOKEN
            rows = gallery_rows(admin=admin)
            self._send(
                200,
                GALLERY_PAGE.replace("{css}", PAGE_CSS)
                .replace("{rows}", rows)
                .replace("{nav}", NAV),
            )
        elif path == "/materials":
            self._send(200, materials_page())
        elif path.startswith("/mat/"):
            self.serve_material(path[len("/mat/"):])
        elif path.startswith("/download/"):
            self.serve_file(path.split("/")[-1])
        elif path.startswith("/delete/"):
            self.delete_item(parsed)
        elif path == "/materials-admin":
            qs = urllib.parse.parse_qs(parsed.query)
            tok = qs.get("admin", [""])[0]
            if tok != ADMIN_TOKEN:
                self._send(200, admin_login_page(wrong=("admin" in qs)))
                return
            if qs.get("action", [""])[0] == "delete":
                self._admin_delete(tok, qs)
                return
            edit = qs.get("edit", [""])[0] or None
            self._send(200, admin_dashboard(tok, edit))
        else:
            self._send(404, "<h1>404 Not Found</h1>")

    def serve_file(self, id_str):
        try:
            rid = int(id_str)
        except ValueError:
            self._send(404, "bad id")
            return
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT filename,stored_name FROM submissions WHERE id=?", (rid,)
        ).fetchone()
        conn.close()
        if not row:
            self._send(404, "not found")
            return
        fname, stored = row
        fpath = os.path.join(UPLOAD_DIR, stored)
        if not os.path.isfile(fpath):
            self._send(404, "file missing")
            return
        with open(fpath, "rb") as f:
            data = f.read()
        encoded = urllib.parse.quote(fname or "file")
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Content-Disposition",
            f"attachment; filename*=UTF-8''{encoded}",
        )
        self.end_headers()
        self.wfile.write(data)

    def serve_material(self, slug):
        """Serve a course-material file.

        The slug is resolved against materials_data.json (a fixed whitelist),
        never from raw user input, so directory traversal is impossible.
        """
        M = load_materials()
        entry = None
        for it in M["lectures"] + M["sections"]:
            if it.get("slug") == slug:
                entry = it
                break
        if not entry:
            self._send(404, "<h1>404 Not Found</h1>")
            return
        fpath = os.path.join(BASE_DIR, entry.get("file", ""))
        if not fpath or not os.path.isfile(fpath):
            self._send(404, "<h1>404 Not Found</h1>")
            return
        with open(fpath, "rb") as f:
            data = f.read()
        fname = os.path.basename(fpath)
        is_pdf = fname.lower().endswith(".pdf")
        # PDFs are shown inline so students can preview them in the browser;
        # everything else (pptx, ...) is downloaded.
        disp = "inline" if is_pdf else "attachment"
        ctype = "application/pdf" if is_pdf else "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Content-Disposition",
            f"{disp}; filename*=UTF-8''{urllib.parse.quote(fname)}",
        )
        self.end_headers()
        self.wfile.write(data)

    def delete_item(self, parsed):
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("admin", [""])[0] != ADMIN_TOKEN:
            self._send(403, "<h1>403 Forbidden</h1><p>需要管理员 token。</p>")
            return
        try:
            rid = int(parsed.path.split("/")[-1])
        except ValueError:
            self._send(404, "bad id")
            return
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT stored_name FROM submissions WHERE id=?", (rid,)
        ).fetchone()
        if row:
            try:
                os.remove(os.path.join(UPLOAD_DIR, row[0]))
            except OSError:
                pass
            conn.execute("DELETE FROM submissions WHERE id=?", (rid,))
            conn.commit()
        conn.close()
        self.send_response(302)
        self.send_header("Location", "/gallery?admin=" + ADMIN_TOKEN)
        self.send_header("Content-Length", "0")
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

    def do_POST(self):
        # Some clients/proxies (curl, and Render's edge proxy) send
        # "Expect: 100-continue" and wait for a 100 response BEFORE sending the
        # request body. If we stay silent, we wait for the body while they wait
        # for the 100 -> deadlock / read timeout. Always acknowledge it first.
        if (self.headers.get("Expect") or "").strip().lower() == "100-continue":
            self.send_response_only(100)
            self.end_headers()
        if self.path == "/upload":
            try:
                self._handle_upload()
            except Exception:
                import traceback
                sys.stderr.write(traceback.format_exc())
                self._send(500, "<h1>500 服务器错误</h1><p>上传处理失败，请重试或联系管理员。</p>")
        elif self.path == "/materials-admin":
            try:
                self.admin_action()
            except Exception:
                import traceback
                sys.stderr.write(traceback.format_exc())
                self._send(500, "<h1>500 服务器错误</h1><p>保存失败，请重试。</p>")
        else:
            self._send(404, "<h1>404</h1>")

    def _admin_delete(self, token, qs):
        kind = qs.get("kind", [""])[0]
        M = load_materials()
        if kind in ("lecture", "section"):
            slug = qs.get("slug", [""])[0]
            lst = M[kind + "s"]
            item = next((x for x in lst if x.get("slug") == slug), None)
            if item:
                fp = os.path.join(BASE_DIR, item.get("file", ""))
                if "materials_uploads" in item.get("file", "") and os.path.isfile(fp):
                    try:
                        os.remove(fp)
                    except OSError:
                        pass
                lst.remove(item)
                save_materials(M)
        elif kind in ("syllabus", "textbook"):
            try:
                idx = int(qs.get("idx", ["-1"])[0])
            except ValueError:
                idx = -1
            lst = M["textbooks"] if kind == "textbook" else M["syllabus"]
            if 0 <= idx < len(lst):
                lst.pop(idx)
                save_materials(M)
        self.send_response(302)
        self.send_header("Location", "/materials-admin?admin=" + token)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def admin_action(self):
        if (self.headers.get("Expect") or "").strip().lower() == "100-continue":
            self.send_response_only(100)
            self.end_headers()
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r"boundary=([^;]+)", ctype)
        if not m:
            self._send(400, "bad request")
            return
        boundary = m.group(1).strip().encode()
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        parts = parse_multipart(body, boundary)
        fields = {}
        files = []
        for p in parts:
            if p["filename"]:
                files.append(p)
            elif p["name"]:
                fields[p["name"]] = p["content"].decode("utf-8", "replace").strip()
        if fields.get("admin") != ADMIN_TOKEN:
            self._send(403, "<h1>403 Forbidden</h1>")
            return
        action = fields.get("action", "")
        M = load_materials()
        if action == "upload":
            self._admin_upload(M, fields, files)
        elif action == "edit":
            self._admin_edit(M, fields, files)
        elif action == "add_syllabus":
            M["syllabus"].append({
                "chapter": fields.get("chapter", ""),
                "zh": fields.get("zh", ""),
                "en": fields.get("en", ""),
                "hours": fields.get("hours", ""),
                "term": fields.get("term", ""),
                "status": fields.get("status", "todo"),
            })
            save_materials(M)
        elif action == "add_textbook":
            M["textbooks"].append({
                "kind": fields.get("kind", ""),
                "title": fields.get("title", ""),
                "note": fields.get("note", ""),
            })
            save_materials(M)
        self.send_response(302)
        self.send_header("Location", "/materials-admin?admin=" + ADMIN_TOKEN)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _admin_upload(self, M, fields, files):
        kind = fields.get("kind", "lecture")
        f = next((p for p in files if p["filename"]), None)
        if not f:
            return
        orig = sanitize_filename(f["filename"])
        base, ext = os.path.splitext(orig)
        stored = orig
        i = 2
        while os.path.exists(os.path.join(MATERIALS_UPLOAD_DIR, stored)):
            stored = base + "_" + str(i) + ext
            i += 1
        with open(os.path.join(MATERIALS_UPLOAD_DIR, stored), "wb") as fh:
            fh.write(f["content"])
        existing = {x.get("slug") for x in (M["lectures"] + M["sections"])}
        slug = _new_slug(os.path.splitext(base)[0], existing)
        rel = "materials_uploads/" + stored
        if kind == "lecture":
            M["lectures"].append({
                "slug": slug, "file": rel,
                "title_zh": fields.get("title_zh", ""),
                "title_en": fields.get("title_en", ""),
                "pages": fields.get("pages", ""),
            })
        else:
            M["sections"].append({
                "slug": slug, "section": fields.get("section", ""),
                "title_zh": fields.get("title_zh", ""),
                "title_en": fields.get("title_en", ""),
                "file": rel,
            })
        save_materials(M)

    def _admin_edit(self, M, fields, files):
        kind = fields.get("kind", "")
        if kind in ("lecture", "section"):
            slug = fields.get("slug", "")
            item = next((x for x in M[kind + "s"] if x.get("slug") == slug), None)
            if not item:
                return
            item["title_zh"] = fields.get("title_zh", item.get("title_zh", ""))
            item["title_en"] = fields.get("title_en", item.get("title_en", ""))
            if kind == "section":
                item["section"] = fields.get("section", item.get("section", ""))
            else:
                item["pages"] = fields.get("pages", item.get("pages", ""))
            f = next((p for p in files if p["filename"]), None)
            if f:
                orig = sanitize_filename(f["filename"])
                base, ext = os.path.splitext(orig)
                stored = orig
                j = 2
                while os.path.exists(os.path.join(MATERIALS_UPLOAD_DIR, stored)):
                    stored = base + "_" + str(j) + ext
                    j += 1
                with open(os.path.join(MATERIALS_UPLOAD_DIR, stored), "wb") as fh:
                    fh.write(f["content"])
                item["file"] = "materials_uploads/" + stored
            save_materials(M)
        elif kind in ("syllabus", "textbook"):
            try:
                idx = int(fields.get("idx", "-1"))
            except ValueError:
                return
            lst = M[kind]
            if not (0 <= idx < len(lst)):
                return
            if kind == "syllabus":
                lst[idx]["chapter"] = fields.get("chapter", lst[idx].get("chapter", ""))
                lst[idx]["zh"] = fields.get("zh", lst[idx].get("zh", ""))
                lst[idx]["en"] = fields.get("en", lst[idx].get("en", ""))
                lst[idx]["hours"] = fields.get("hours", lst[idx].get("hours", ""))
                lst[idx]["term"] = fields.get("term", lst[idx].get("term", ""))
                lst[idx]["status"] = fields.get("status", lst[idx].get("status", "todo"))
            else:
                lst[idx]["kind"] = fields.get("kind_v", lst[idx].get("kind", ""))
                lst[idx]["title"] = fields.get("title", lst[idx].get("title", ""))
                lst[idx]["note"] = fields.get("note", lst[idx].get("note", ""))
            save_materials(M)

    def _handle_upload(self):
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r"boundary=([^;]+)", ctype)
        if not m:
            self._send(400, "bad request")
            return
        boundary = m.group(1).strip().encode()
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_SIZE + 8 * 1024 * 1024:
            self._send(413, "<h1>文件过大</h1>")
            return
        body = self.rfile.read(length)
        parts = parse_multipart(body, boundary)

        fields = {}
        files = []
        for p in parts:
            if p["filename"]:
                files.append(p)
            elif p["name"]:
                fields[p["name"]] = p["content"].decode("utf-8", "replace").strip()

        name = fields.get("name", "")
        sid = fields.get("student_id", "")
        note = fields.get("note", "")
        if not name or not sid:
            self._send(400, "<h1>请填写姓名和学号</h1><p><a href='/'>返回</a></p>")
            return
        if not files:
            self._send(400, "<h1>请选择文件</h1><p><a href='/'>返回</a></p>")
            return

        ext_check = "ok"
        conn = sqlite3.connect(DB_PATH)
        saved = 0
        for p in files:
            raw = p["content"]
            if len(raw) > MAX_SIZE:
                ext_check = "skip"
                continue
            orig = sanitize_filename(p["filename"])
            if ALLOWED_EXT and os.path.splitext(orig)[1].lower() not in ALLOWED_EXT:
                continue
            stored = f"{sid}_{uuid.uuid4().hex}_{orig}"
            with open(os.path.join(UPLOAD_DIR, stored), "wb") as f:
                f.write(raw)
            conn.execute(
                "INSERT INTO submissions(name,student_id,note,filename,stored_name,size,ts) VALUES(?,?,?,?,?,?,?)",
                (name, sid, note, orig, stored, len(raw), now_str()),
            )
            saved += 1
        conn.commit()
        conn.close()

        if saved == 0:
            self._send(400, "<h1>没有可保存的文件（可能超过大小限制）</h1><p><a href='/'>返回</a></p>")
            return
        self.send_response(302)
        self.send_header("Location", "/?ok=1")
        # Must declare an empty body: with HTTP/1.1 keep-alive, a response
        # without Content-Length/Transfer-Encoding leaves the client waiting
        # forever for a body that never comes (curl, scripts and Render's proxy
        # all hang; browsers happen to follow Location immediately, which is
        # why this bug hid during manual testing).
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Courseware portal running at http://localhost:{PORT}")
    print(f"  upload : http://localhost:{PORT}/")
    print(f"  gallery: http://localhost:{PORT}/gallery")
    print(f"  admin  : http://localhost:{PORT}/gallery?admin={ADMIN_TOKEN}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
