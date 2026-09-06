# 课件上传门户 · Courseware Upload Portal

一个**零依赖**的网页应用，用于把课件 / 作业上传并分享给学生（也支持学生提交作业给老师）。
只用 Python 标准库，无需安装任何第三方包。

> 本仓库同时收录了《高等数学》（同济第 8 版）配套英文 Beamer 课件（见 `slides/`）。

---

## 功能

- **上传页** `/`：填写 **姓名 + 学号 + 备注**，支持拖拽 / 多选上传文件（ppt/pptx/pdf/doc/图片/压缩包等，单文件上限 200 MB）。
- **作品墙** `/gallery`：所有提交公开陈列，可按姓名 / 学号 / 备注搜索、一键下载。
- **教师管理**：访问 `/gallery?admin=<你的TOKEN>` 可删除某条提交。
- 文件保存在 `uploads/`，提交元信息保存在 SQLite 数据库 `submissions.db`。

---

## 本地运行

```bash
cd courseware_portal
python app.py
```

打开 http://localhost:8000 即可。

可用环境变量（均有默认值）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HOST` | `0.0.0.0` | 监听地址 |
| `PORT` | `8000` | 监听端口（Render 会自动注入） |
| `ADMIN_TOKEN` | `change-me-2026` | 管理员删除口令 **部署前务必修改** |
| `MAX_SIZE` | `209715200` | 单文件大小上限（字节，默认 200 MB） |

示例：

```bash
ADMIN_TOKEN=my-secret-2026 PORT=9000 python app.py
```

---

## 部署到 Render（公网访问）

1. 注册并登录 [render.com](https://render.com)，用 GitHub 授权登录。
2. 点击 **New → Web Service**，选择本仓库 `Xueren2023/courseware-portal`。
3. 配置：
   - **Runtime**：`Python`
   - **Build Command**：留空（零依赖，无需安装）
   - **Start Command**：`python app.py`
4. 展开 **Advanced → Add Environment Variable**，添加：
   - `ADMIN_TOKEN` = 你自己的强密码（⚠️ 务必修改，默认 `change-me-2026` 公网任何人都可删除作业）
   - `PORT` 不需要填，Render 会自动注入
5. 点击 **Create Web Service**，等待 1–2 分钟构建完成。
6. 页面顶部会显示形如 `https://courseware-portal-xxxx.onrender.com` 的公网地址——这就是最终分享给学生的网址。

> 免费版 Render 在一段时间无访问后会「休眠」，下次访问需冷启动几秒，属正常现象。

---

## ⚠️ 安全提醒

- 部署完成后，请到 GitHub → **Settings → Developer settings → Personal access tokens** 删除本次用于推送的 token（用完即焚）。
- 务必修改 `ADMIN_TOKEN`，否则公网任何人都可通过 `?admin=change-me-2026` 删除学生提交的作业。
- 学生提交的作业数据（`uploads/`、`submissions.db`）**不会进入本仓库**（已在 `.gitignore` 忽略），仅保存在运行服务的服务器上。

---

## 仓库结构

```
courseware_portal/
├── app.py                 # 网页应用（零依赖，标准库）
├── Procfile               # Render 启动命令：web: python app.py
├── requirements.txt       # 空依赖声明（兼容性）
├── runtime.txt            # 指定 Python 版本
├── .gitignore             # 忽略 uploads/、submissions.db、日志
├── README.md              # 本文件
└── slides/                # 配套英文 Beamer 课件（.tex 源码 + 编译后的 .pdf）
    ├── ch1_intro_beamer.tex / .pdf          # Chapter 1：引言 + 映射与函数（137 页）
    ├── ch2_limits_beamer.tex / .pdf         # Chapter 2：数列的极限 + 函数的极限（151 页）
    └── ch2_limits_cont_beamer.tex / .pdf    # Chapter 2 续：无穷小与无穷大 + 极限运算法则 + 无穷小比较（141 页）
```

---

## 重新编译课件（如需改动）

课件为 Beamer 文档，使用 `pdflatex` 编译两遍（生成目录 / 交叉引用）：

```bash
cd slides
pdflatex ch2_limits_cont_beamer.tex
pdflatex ch2_limits_cont_beamer.tex
```
