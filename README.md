<div align="center">

# forum-thread-to-pdf

**将国内主流论坛与内容社区的帖子/文章抓取、清洗去重并排版导出为高清 A4 PDF**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Renderer-Playwright%20%2B%20Edge%2FChrome-2EAD33?logo=playwright&logoColor=white)](https://playwright.dev/python/)
[![Agent Skill](https://img.shields.io/badge/Agent-Skill%20Ready-6366F1)](#作为-agent-skill-安装)

[支持平台](#支持平台与兼容状态) · [核心特性](#核心特性) · [快速开始](#快速开始) · [参数与退出码](#参数与退出码) · [工作原理](#工作原理)

</div>

---

## 简介

`forum-thread-to-pdf` 是一个专为中文社区场景设计的 **Agent Skill** 与 **命令行导出工具**。  
它会自动识别链接所属平台，处理多页翻页、编码差异、置顶/推荐回帖重复、CDN 防盗链配图以及网页端杂讯，最终通过无头浏览器渲染为带有页码页脚的 A4 PDF 文档。

---

## 支持平台与兼容状态

### 已适配并实测通过的平台

| 平台 | 链接示例 | 抓取方式与适配要点 | 默认布局 |
| --- | --- | --- | --- |
| **Discuz! 论坛**<br><sub>（已实测虫部落、吾爱破解；兼容常见 Discuz URL）</sub> | `mod=viewthread&tid=...`<br>`thread-<tid>-1-1.html` | 自动嗅探 `UTF-8 / GBK / GB18030` 编码；支持多页抓取；对顶部“推荐回帖”去重并按 `post_id` 时序重排；兼容 `div.blockcode` 代码块与 `<ignore_js_op>` 图片；主帖显式使用仿宋时自动启用仿宋排版 | 自适应 `article` / `qa` |
| **V2EX** | `v2ex.com/t/<tid>` | 调用官方公开 JSON API（`topics/show.json` + `replies/show.json`），避开网页端 403 限制并完整拉取楼层 | `qa` / 自适应 |
| **虎扑社区** | `bbs.hupu.com/<tid>.html` | 解析 SSR `__NEXT_DATA__` JSON；支持多页回帖翻页、按 `pid` 去重、保留引用回复与 `亮了 ×N` 点赞标记 | `qa` / 自适应 |
| **少数派** | `sspai.com/post/<id>` | 调用文章详情与热门评论 API；自动携带 `Referer` 本地化拉取 `cdnfile.sspai.com` 配图（公开免费文章） | `article` |
| **微信公众号** | `mp.weixin.qq.com/s/...` | 提取 `#activity-name`、`#js_name`、`#js_content` 与发布时间 `ct`；还原 `data-src` 懒加载图片并无 `Referer` 本地化 | `article` |
| **博客园** | `cnblogs.com/<blog>/p/<id>` | 提取 `#cb_post_title_url`、`#post-date` 与 `#cnblogs_post_body`，保留代码块、表格与配图 | `article` |
| **稀土掘金** | `juejin.cn/post/<id>` | 通过 Playwright 无头浏览器渲染提取 `.markdown-body` 正文、配图与可见评论区（最多前 15 条） | `article` |

### 受访问控制 / 人机验证限制的平台

> 本工具**严格遵守目标站点的访问边界**，不会绕过登录鉴权或破解人机验证。遇到以下情况时脚本会返回退出码 `2` 并给出明确提示：

- **小黑盒（`xiaoheihe.cn`）**：已在 [adapters.py](scripts/adapters.py) 中内置识别逻辑；当详情接口 `/bbs/app/link/tree` 返回 `status: show_captcha` 时自动中止并提示需人工验证。
- **豆瓣小组 / 百度贴吧 / NGA / Linux.do 等**：匿名抓取返回 `HTTP 401 / 403 / 429` 时直接抛出 `AccessBlockedError` 并中止。

---

## 核心特性

- **双排版模式自适应（`--mode auto`）**：
  - **`article`（文章模式）**：当主帖纯文本长度 `≥ 600` 字符且收录回复 `≤ 20` 条时自动启用，突出正文阅读体验，回复以附录形式置于文末。
  - **`qa`（问答盖楼模式）**：适合多楼层互动讨论，主帖置顶卡片展示，回复按楼层依次排布并高亮 `楼主`、`推荐` 与 `亮了 ×N` 标签及引用块。
- **并发图片本地化缓存**：渲染前使用线程池并发下载正文图片到临时目录，针对不同图床（如少数派需携带 `Referer`、微信需无 `Referer`）自动回退重试并改写为本地 `file://` 路径，避免 PDF 中出现裂图，渲染结束后自动清理临时目录。
- **语义 HTML 清洗**：保留标题、段落、表格、代码块、列表、引用与超链接，自动剥离广告脚本、`display:none` 隐藏文本、Discuz `.jammer` 防复制干扰字符、附件提示浮层以及表情/文件类型小图标。
- **页码与分页校验**：内置 A4 页边距与页脚页码（`第 X 页 / 共 Y 页`）；配合 `--verify-dir` 可利用 PyMuPDF 输出每页字数统计及首页/末页 PNG 预览图，方便排查尾页孤立溢出。

---

## 快速开始

### 1. 环境准备

需要 **Python 3.10+**，并安装依赖：

```bash
pip install -r requirements.txt
```

`requirements.txt` 包含：
- `beautifulsoup4>=4.12.0`
- `lxml>=5.0.0`
- `playwright>=1.40.0`
- `pymupdf>=1.24.0`

> PDF 渲染优先调用本机已安装的 **Microsoft Edge** 或 **Google Chrome**；若本机均未安装，请执行 `playwright install chromium` 安装内置浏览器。

### 2. 作为 Agent Skill 安装

将本仓库目录放置到 Agent 的 Skills 目录即可被自动发现：

- **全局安装**：`~/.gemini/config/skills/forum-thread-to-pdf`
- **项目级安装**：`<项目根目录>/.agents/skills/forum-thread-to-pdf`

### 3. 命令行使用示例

```bash
# Discuz! 论坛（支持 UTF-8 虫部落与 GBK 吾爱破解）
python scripts/thread_to_pdf.py "https://www.chongbuluo.com/forum.php?mod=viewthread&tid=25377"
python scripts/thread_to_pdf.py "https://www.52pojie.cn/thread-2117334-1-1.html" --max-pages 1

# V2EX / 虎扑社区（多页讨论帖）
python scripts/thread_to_pdf.py "https://www.v2ex.com/t/1257339"
python scripts/thread_to_pdf.py "https://bbs.hupu.com/643983153.html" --max-pages 2 --replies all

# 少数派 / 微信公众号 / 博客园 / 稀土掘金
python scripts/thread_to_pdf.py "https://sspai.com/post/118100"
python scripts/thread_to_pdf.py "https://mp.weixin.qq.com/s/8Zzn4wMBENZAPSdS6tT2Sg"
python scripts/thread_to_pdf.py "https://www.cnblogs.com/hugogoos/p/23668983"
python scripts/thread_to_pdf.py "https://juejin.cn/post/7700887980140429322"

# 自定义输出路径并生成首末页预览校验图
python scripts/thread_to_pdf.py "https://www.chongbuluo.com/forum.php?mod=viewthread&tid=23914" \
  -o "./output/虫卦摊.pdf" \
  --mode qa \
  --verify-dir "./verify"
```

---

## 参数与退出码

### CLI 参数

| 参数 | 可选值 / 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `url` | 字符串（必填） | — | 目标帖子或文章的完整 URL |
| `-o, --output` | 文件路径 | `./<帖子标题>.pdf` | 输出 PDF 路径（自动替换文件名中的系统非法字符） |
| `--mode` | `auto` \| `article` \| `qa` | `auto` | 排版模式 |
| `--replies` | `all` \| `lz-only` \| `none` | `all` | 回复收录范围（全部回复 / 仅看楼主 / 仅主帖） |
| `--max-pages` | 整数 | `10` | 多页帖子最多抓取的页数；设为 `0` 表示抓取全部分页 |
| `--html-out` | 文件路径 | 不保留 | 保留生成的中间 HTML 文件，便于手工调整样式 |
| `--verify-dir` | 目录路径 | 不生成 | 使用 PyMuPDF 统计每页字符数并导出首页、末页 PNG 预览 |

### 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 抓取与 PDF 生成成功 |
| `1` | 网络、解析或浏览器运行期异常 |
| `2` | 平台暂不支持，或触发登录 / 403 / `show_captcha` 访问限制 |

---

## 工作原理

```mermaid
flowchart LR
    U["输入帖子 URL"] --> R["adapters.py<br/>按域名识别适配器"]
    R --> F["抓取页面 / 公开 API<br/>编码嗅探 · 多页合并 · 楼层去重"]
    F --> C["core.py<br/>clean_rich_html 语义清洗"]
    C --> I["并发图片本地化<br/>自动切换 Referer 策略"]
    I --> P["Playwright (Edge / Chrome)<br/>自适应 article / qa 输出 A4 PDF"]
    P --> V["--verify-dir (可选)<br/>PyMuPDF 统计字数 & 首末页预览"]
```

### 目录结构

```text
forum-thread-to-pdf/
├── SKILL.md                  # Agent Skill 触发与操作规范
├── README.md                 # 项目文档
├── requirements.txt          # Python 依赖声明
└── scripts/
    ├── core.py               # 数据模型、编码嗅探、HTML 清洗、图片本地化与 PDF 渲染
    ├── adapters.py           # 平台适配器与 URL 路由分发
    └── thread_to_pdf.py      # 命令行入口
```
