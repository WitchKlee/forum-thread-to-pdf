---
name: forum-thread-to-pdf
description: 将国内主流论坛与内容社区帖子（虫部落/吾爱破解等 Discuz! 论坛、V2EX、虎扑、少数派、微信公众号、博客园、稀土掘金）完整抓取、清洗去重并排版导出为高清 A4 PDF。当用户提供这些平台的帖子或文章链接并要求“做成 PDF”、“导出 PDF”、“保存帖子为 PDF”或归档多页讨论时使用。
---

# 主流论坛与社区帖子转 PDF（forum-thread-to-pdf）

支持自动识别 URL 所属平台，完成多页抓取、推荐回帖去重、图片防盗链本地化缓存、HTML 语义清洗与 A4 PDF 排版导出。

## 已适配平台与抓取策略

| 平台 | 典型 URL 模式 | 抓取与适配机制 |
| --- | --- | --- |
| **Discuz! 论坛**（虫部落、吾爱破解等） | `mod=viewthread&tid=...` 或 `thread-<tid>-1-1.html` | 自动嗅探 `UTF-8 / GBK / GB18030` 编码；多页翻页；去除顶部“推荐回帖”重复项并按 `post_id` 重排；兼容 `div.blockcode` 代码块与 `<ignore_js_op>` 图片 |
| **V2EX** | `v2ex.com/t/<tid>` | 调用公开 JSON API（`/api/topics/show.json` + `/api/replies/show.json`）获取主帖与全部楼层 |
| **虎扑社区** | `bbs.hupu.com/<tid>.html` | 解析 Next.js `__NEXT_DATA__` SSR 数据，支持多页翻页、楼层去重、引用与“亮了 ×N”标记 |
| **少数派** | `sspai.com/post/<id>` | 调用文章详情与热评 API，自动附带 `Referer` 拉取 `cdnfile.sspai.com` 配图 |
| **微信公众号** | `mp.weixin.qq.com/s/...` | 提取 `#activity-name` / `#js_name` / `#js_content` 与发布时间戳，还原 `data-src` 懒加载图片 |
| **博客园** | `cnblogs.com/<blog>/p/<id>` | 提取 `#cnblogs_post_body`，保留技术代码块、表格与配图 |
| **稀土掘金** | `juejin.cn/post/<id>` | 通过 Playwright 无头浏览器渲染 `.markdown-body` 正文、配图与评论 |

> 若目标平台返回 403 / 需登录 / 人机验证（如部分豆瓣小组、贴吧、NGA、Linux.do 帖子），脚本会抛出 `AccessBlockedError` 并以退出码 `2` 清晰提示；**严禁尝试绕过登录或人机验证**。

## 前置依赖

```powershell
python -c "import bs4, lxml, playwright, pymupdf; print('OK')"
```

如缺少模块：`pip install beautifulsoup4 lxml playwright pymupdf`（渲染引擎优先复用本机 Edge 或 Chrome，也可执行 `playwright install chromium`）。

## 标准执行流程

### 第 1 步：确定参数与输出路径

```powershell
python "<本skill目录>/scripts/thread_to_pdf.py" "<帖子或文章URL>" -o "<输出PDF路径>" --verify-dir "<临时预览目录>"
```

常用参数：
- `--mode auto|article|qa`：排版模式。`auto` 在“主帖 ≥ 600 字且回复 ≤ 20 条”时自动选用 **文章模式（`article`）**，否则选用 **问答盖楼模式（`qa`）**。
- `--replies all|lz-only|none`：控制回复收录范围（全部回复 / 仅看楼主 / 仅主帖）。
- `--max-pages <N>`：多页帖子抓取页数上限（默认 `10`，设 `0` 可抓取全部分页，防止数十页长贴耗时过长）。
- `--html-out <路径>`：保存清洗后的中间 HTML，便于手工微调后重出 PDF。
- `--verify-dir <目录>`：导出后利用 PyMuPDF 统计每页字符数并生成首页、末页 PNG 预览。

### 第 2 步：导出后质量校验（必做）

1. **检查图片加载**：用 `read_file` 查看 `--verify-dir` 生成的首页与末页 PNG 预览图，确认所有内嵌图片已正确渲染（无裂图图标）。脚本已在渲染前自动按平台切换 `Referer` 并发下载图片到本地临时目录，如遇极端超时可复查。
2. **检查末页孤立溢出**：查看终端打印的 `Chars/page` 列表。若纯文本讨论贴最后一页字符极少（如仅溢出 1~2 行），可保留 `--html-out` 微调边距或字号后再输出。
3. **检查长贴分页范围**：若原帖超过 `--max-pages` 上限，确认是否需要传 `--max-pages 0` 或 `--replies lz-only` 完整归档。
