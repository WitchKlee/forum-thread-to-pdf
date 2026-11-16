# forum-thread-to-pdf

将 Discuz! 论坛帖子（如虫部落 `chongbuluo.com`）完整抓取、去重整理并排版导出为高清 A4 PDF 的 Agent Skill 与命令行工具。

## 特性

- **自动翻页抓取**：自动解析帖子总页数（支持单页长文与多页盖楼讨论）。
- **推荐回帖去重 & 时序重排**：自动去除 Discuz! 置顶“推荐回帖”产生的重复楼层，按 `post_id` 时间顺序严格排列。
- **双排版模式自适应**：
  - `article`（长文模式）：适合 1 楼为长篇心得/教程、回复较少的帖子，保留小标题层级与仿宋首行缩进正文排版，文末附后续回复。
  - `qa`（问答盖楼模式）：适合多页互动、问答盖楼贴，按楼层卡片排版，高亮`楼主`标签、编辑说明与引用块（Quote）。
- **保留细节与校验**：保留 `补充内容`、内嵌图片（兼容懒加载 `file` 属性）及页码页脚，并支持输出首末页 PNG 预览用于分页检查。

## 目录结构

```text
forum-thread-to-pdf/
├── SKILL.md                  # Agent Skill 指令文件
├── README.md                 # 项目说明
├── requirements.txt          # Python 依赖
└── scripts/
    └── thread_to_pdf.py      # 抓取与 PDF 渲染主脚本
```

## 安装依赖

```bash
pip install -r requirements.txt
```

> 渲染引擎使用 Playwright 调用本机已安装的 Microsoft Edge 或 Google Chrome；若本机均未安装，请额外执行 `playwright install chromium`。

## 作为 Agent Skill 安装

将本仓库克隆或复制到全局 Skills 目录（例如 `~/.gemini/config/skills/forum-thread-to-pdf`）或项目工作区的 `.agents/skills/forum-thread-to-pdf` 即可被自动发现。

## 命令行直接使用

```bash
# 自动判断文章/问答模式并导出 PDF
python scripts/thread_to_pdf.py "https://www.chongbuluo.com/forum.php?mod=viewthread&tid=25377" -o output.pdf

# 强制使用问答模式，仅保留楼主回复，并输出首末页预览图
python scripts/thread_to_pdf.py "https://www.chongbuluo.com/forum.php?mod=viewthread&tid=23914" --mode qa --replies lz-only --verify-dir ./verify
```

### 常用参数

| 参数 | 可选值 | 说明 |
| --- | --- | --- |
| `-o, --output` | 文件路径 | 输出 PDF 路径（默认当前目录 `<帖子标题>.pdf`） |
| `--mode` | `auto` / `article` / `qa` | 排版模式（默认 `auto` 自动判断） |
| `--replies` | `all` / `lz-only` / `none` | 回复收录范围（全部 / 仅看楼主 / 仅主帖） |
| `--html-out` | 文件路径 | 保存中间 HTML 以便微调样式 |
| `--verify-dir` | 目录路径 | 导出后生成页数统计与首末页 PNG 预览图 |
