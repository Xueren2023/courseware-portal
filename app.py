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

All user-supplied text is HTML-escaped before rendering (no XSS).
"""

import os
import re
import io
import sys
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
"""

UPLOAD_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>课件上传</title><style>{css}</style></head>
<body>
<header><h1>课件上传</h1><p>填写信息并上传文件，提交后会出现在作品墙</p></header>
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
<div class="topnav"><a href="/">＋ 去上传</a></div>
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
            self._send(200, UPLOAD_PAGE.replace("{css}", PAGE_CSS).replace("{card}", card))
        elif path == "/gallery":
            admin = urllib.parse.parse_qs(parsed.query).get("admin", [""])[0] == ADMIN_TOKEN
            rows = gallery_rows(admin=admin)
            self._send(200, GALLERY_PAGE.replace("{css}", PAGE_CSS).replace("{rows}", rows))
        elif path.startswith("/download/"):
            self.serve_file(path.split("/")[-1])
        elif path.startswith("/delete/"):
            self.delete_item(parsed)
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
        if self.path != "/upload":
            self._send(404, "<h1>404</h1>")
            return
        try:
            self._handle_upload()
        except Exception:
            import traceback
            sys.stderr.write(traceback.format_exc())
            self._send(500, "<h1>500 服务器错误</h1><p>上传处理失败，请重试或联系管理员。</p>")

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
