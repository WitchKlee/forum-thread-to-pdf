---
name: forum-thread-to-pdf
description: 将 Discuz! 论坛帖子（如虫部落 chongbuluo.com）完整抓取、去重整理并排版导出为高清 A4 PDF。当用户提供论坛帖子链接并要求“做成 PDF”、“导出 PDF”、“保存帖子为 PDF”或归档多页论坛讨论时使用。
---

# 论坛帖子转 PDF（forum-thread-to-pdf）

将 Discuz! 类论坛帖子（支持单页长文与多页盖楼问答）自动翻页抓取、对顶部“推荐回帖”去重并按时间重排、清理导航与广告杂讯，最终渲染为适合阅读与存档的 A4 PDF。

## 前置依赖

运行脚本前确认 Python 环境已安装以下依赖（Windows 自带 Edge 即可作为无头浏览器渲染引擎）：

```powershell
python -c "import bs4, lxml, playwright, pymupdf; print('OK')"
```

如缺少模块，按需安装：`pip install beautifulsoup4 lxml playwright pymupdf`（以及 `playwright install chromium`，若本机已安装 Edge/Chrome 则脚本会优先直接调用）。

## 标准执行流程

### 第 1 步：确认输出路径与模式

- 默认输出目录为当前工作区根目录，文件名为 `<帖子标题>.pdf`（自动过滤 Windows 非法文件名字符）。
- 两种自适应排版模式（`--mode auto` 自动判断，也可手动指定）：
  1. **文章模式（`article`）**：适合 1 楼为长文分享、回复较少的帖子（如心得/教程贴）。保留小标题层级、首行缩进（仿宋正文排版），文末附后续回复。
  2. **问答盖楼模式（`qa`）**：适合多页互动、问答盖楼贴。自动翻页并按 `post_id` 时间顺序排列，对 Discuz 置顶“推荐回帖”自动去重，高亮`楼主`身份标签、编辑说明与引用块（Quote）。

### 第 2 步：运行导出脚本

```powershell
python "<本skill目录>/scripts/thread_to_pdf.py" "<帖子URL>" -o "<输出PDF路径>" --verify-dir "<临时预览目录>"
```

常用可选参数：
- `--mode auto|article|qa`：强制指定排版样式（默认 `auto`）
- `--replies all|lz-only|none`：控制回复收录范围（全部回复 / 仅看楼主 / 仅主帖）
- `--html-out <路径>`：保留生成的中间 HTML，便于微调样式后重出 PDF

### 第 3 步：视觉与分页质量校验（必做）

脚本通过 `--verify-dir` 会输出总页数、每页字符数以及首页与末页的 PNG 预览图。导出后必须检查：

1. **末页孤立溢出**：如果末页字符数极少（例如 `< 150` 字符，仅溢出 1 条短回复或 1~2 行正文），可通过 `--html-out` 保存 HTML 后微调 `font-size`、`line-height` 或 `@page` 上下边距重新渲染，避免出现几乎空白的尾页。
2. **元数据换行与图片**：用 `read_file` 查看首页与末页预览图，确认长 URL 未在单词中间丑陋折行、页脚页码（`第 X 页 / 共 Y 页`）完整、帖子内嵌图片正常加载。
3. **登录/反爬兜底**：若脚本提示未解析到帖子内容（例如帖子需要登录查看或触发人机验证），不要尝试绕过验证；直接向用户说明该帖受访问限制，或先改用浏览器确认可见状态。
