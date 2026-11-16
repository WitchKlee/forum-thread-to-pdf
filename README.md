# forum-thread-to-pdf

将国内主流论坛与内容社区的帖子/文章完整抓取、清洗去重并排版导出为高清 A4 PDF 的 Agent Skill 与命令行工具。

## 已支持平台

| 平台 | 支持特性 |
| --- | --- |
| **Discuz! 论坛**（虫部落、吾爱破解、Chiphell 等） | UTF-8 / GBK / GB18030 自动识别、多页抓取、推荐回帖去重、时序重排、代码块与附件图片还原 |
| **V2EX** | 基于公开 API 获取主帖与全部回复楼层，无惧网页端访问限制 |
| **虎扑社区** | 解析 `__NEXT_DATA__` SSR 数据，多页回帖翻页、引用块与`亮了 ×N`标记 |
| **少数派** | 文章详情 + 热门评论 API 提取，自动处理 CDN 防盗链配图 |
| **微信公众号** | 解析正文与发布时间戳，自动还原 `data-src` 懒加载图片并本地化嵌入 |
| **博客园** | 提取完整博文正文、代码块、表格与配图 |
| **稀土掘金** | Playwright 无头渲染提取 Markdown 正文、代码、配图与精选评论 |

## 目录结构

```text
forum-thread-to-pdf/
├── SKILL.md                  # Agent Skill 指令文件
├── README.md                 # 项目说明
├── requirements.txt          # Python 依赖
└── scripts/
    ├── core.py               # 数据模型、编码嗅探、HTML 清洗、图片本地化缓存与 A4 PDF 渲染
    ├── adapters.py           # 各平台解析适配器与路由分发
    └── thread_to_pdf.py      # 命令行入口
```

## 安装依赖

```bash
pip install -r requirements.txt
```

> 渲染引擎使用 Playwright 调用本机已安装的 Microsoft Edge 或 Google Chrome；若本机均未安装，请额外执行 `playwright install chromium`。

## 作为 Agent Skill 安装

将本仓库克隆或复制到全局 Skills 目录（例如 `~/.gemini/config/skills/forum-thread-to-pdf`）或项目工作区的 `.agents/skills/forum-thread-to-pdf` 即可被自动发现。

## 命令行使用示例

```bash
# 1. Discuz!（支持 UTF-8 虫部落 / GBK 吾爱破解）
python scripts/thread_to_pdf.py "https://www.chongbuluo.com/forum.php?mod=viewthread&tid=25377"
python scripts/thread_to_pdf.py "https://www.52pojie.cn/thread-2117334-1-1.html" --max-pages 1

# 2. V2EX / 虎扑社区（问答盖楼模式）
python scripts/thread_to_pdf.py "https://www.v2ex.com/t/1257339"
python scripts/thread_to_pdf.py "https://bbs.hupu.com/643983153.html" --max-pages 2

# 3. 少数派 / 微信公众号 / 博客园 / 稀土掘金（文章模式）
python scripts/thread_to_pdf.py "https://sspai.com/post/118100"
python scripts/thread_to_pdf.py "https://mp.weixin.qq.com/s/8Zzn4wMBENZAPSdS6tT2Sg"
python scripts/thread_to_pdf.py "https://www.cnblogs.com/hugogoos/p/23668983"
python scripts/thread_to_pdf.py "https://juejin.cn/post/7700887980140429322"
```

### 常用参数

| 参数 | 可选值 | 说明 |
| --- | --- | --- |
| `-o, --output` | 文件路径 | 输出 PDF 路径（默认当前目录 `<帖子标题>.pdf`） |
| `--mode` | `auto` / `article` / `qa` | 排版模式（默认 `auto` 自动判断） |
| `--replies` | `all` / `lz-only` / `none` | 回复收录范围（全部 / 仅看楼主 / 仅主帖） |
| `--max-pages` | 整数 | 多页帖子最大抓取页数（默认 `10`，`0` 表示全部） |
| `--html-out` | 文件路径 | 保存清洗后的中间 HTML 以便微调样式 |
| `--verify-dir` | 目录路径 | 导出后生成页数统计与首末页 PNG 预览图 |
