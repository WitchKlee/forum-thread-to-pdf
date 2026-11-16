"""
Core data models, HTTP/charset helpers, rich HTML sanitization, and A4 PDF rendering
shared across all forum/community platform adapters.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import mimetypes
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from bs4 import BeautifulSoup, NavigableString, Tag
from playwright.sync_api import sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

TZ_CN = timezone(timedelta(hours=8))

# Tags preserved in sanitized rich body HTML
ALLOWED_TAGS = {
    "p", "h1", "h2", "h3", "h4", "strong", "b", "em", "i", "del", "s",
    "a", "code", "pre", "blockquote", "ul", "ol", "li",
    "table", "thead", "tbody", "tr", "th", "td",
    "hr", "br", "img", "figure", "figcaption",
}

# Tags whose wrapper is unwrapped (children kept)
UNWRAP_TAGS = {"span", "font", "section", "article", "main", "div", "center", "label", "small", "u"}

# Discuz / forum UI icon paths to ignore when collecting post images
IGNORED_IMG_PATTERNS = (
    "static/image/smiley/",
    "static/image/common/",
    "static/image/filetype/",
    "/images/smilies/",
    "res.wx.qq.com/mmbizwap/",
)


class AccessBlockedError(RuntimeError):
    """Raised when a target thread is gated behind login, WAF, or anti-bot verification."""


class UnsupportedPlatformError(ValueError):
    """Raised when no adapter matches the provided URL."""


@dataclass
class PostItem:
    floor: int
    author: str
    time: str
    body_html: str
    is_lz: bool = False
    is_rec: bool = False
    light_count: int = 0
    quote_text: Optional[str] = None
    pstatus: Optional[str] = None
    text_len: int = 0


@dataclass
class ThreadData:
    platform: str
    domain: str
    source_url: str
    thread_id: str
    title: str
    forum_name: str
    total_pages: int
    posts: list[PostItem] = field(default_factory=list)
    prefer_fangsong: bool = False
    truncated_note: Optional[str] = None


def format_timestamp(ts: int | float | str | None) -> str:
    if ts is None or ts == "":
        return ""
    try:
        val = int(float(ts))
        if val > 10_000_000_000:  # milliseconds
            val //= 1000
        return datetime.fromtimestamp(val, tz=TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name).strip()
    return cleaned[:120] or "forum_thread"


def fetch_text(url: str, extra_headers: Optional[dict[str, str]] = None, timeout: int = 20) -> str:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ct = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429):
            raise AccessBlockedError(f"目标页面返回 HTTP {e.code}（可能需要登录或受反爬/访问限制）：{url}") from e
        raise RuntimeError(f"请求失败 HTTP {e.code}：{url}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络请求失败：{url}（{e.reason}）") from e

    encoding = None
    m_ct = re.search(r"charset=([\w-]+)", ct, re.I)
    if m_ct:
        encoding = m_ct.group(1)
    if not encoding:
        head_snippet = raw[:4096].decode("ascii", errors="ignore")
        m_meta = re.search(r'<meta[^>]+charset=["\']?([\w-]+)', head_snippet, re.I)
        if m_meta:
            encoding = m_meta.group(1)
    if not encoding:
        encoding = "utf-8"

    if encoding.lower() in ("gb2312", "gbk"):
        encoding = "gb18030"
    return raw.decode(encoding, errors="replace")


def fetch_json(url: str, extra_headers: Optional[dict[str, str]] = None, timeout: int = 20):
    text = fetch_text(url, extra_headers=extra_headers, timeout=timeout)
    return json.loads(text)


def _is_hidden_style(style: str) -> bool:
    s = style.replace(" ", "").lower()
    return "display:none" in s or "font-size:0" in s


def _resolve_img_src(img: Tag, base_url: str) -> Optional[str]:
    for attr in ("data-src", "data-original", "data-origin", "file", "zoomfile", "src"):
        val = img.get(attr)
        if val and isinstance(val, str):
            val = val.strip()
            if not val or val.startswith("data:image/gif") or val.startswith("javascript:"):
                continue
            if any(pat in val for pat in IGNORED_IMG_PATTERNS):
                return None
            return urllib.parse.urljoin(base_url, val)
    return None


def _convert_discuz_blockcode(soup: BeautifulSoup) -> None:
    for bc in soup.select("div.blockcode"):
        lines = [li.get_text() for li in bc.select("ol li")]
        code_text = "\n".join(lines) if lines else bc.get_text("\n")
        pre = soup.new_tag("pre")
        code = soup.new_tag("code")
        code.string = code_text.strip("\r\n")
        pre.append(code)
        bc.replace_with(pre)


def clean_rich_html(raw_html: str, base_url: str, promote_strong_p_to_h2: bool = False) -> tuple[str, int]:
    """Sanitize a post/article HTML fragment into clean, uniform semantic HTML."""
    if not raw_html or not raw_html.strip():
        return "", 0

    soup = BeautifulSoup(raw_html, "lxml")

    # Drop scripts, styles, forms, hidden anti-copy text, and Discuz image tooltips
    for sel in (
        "script", "style", "iframe", "noscript", "svg", "button",
        ".aimg_tip", ".tip", ".jammer", ".pstatus", ".quote",
    ):
        for el in soup.select(sel):
            el.decompose()

    for el in list(soup.find_all(True)):
        if el.attrs is None:
            continue
        style = el.get("style", "")
        if style and _is_hidden_style(style):
            el.decompose()

    _convert_discuz_blockcode(soup)

    # Discuz <ignore_js_op>: keep inner <img>, discard attachment metadata clutter
    for ig in soup.select("ignore_js_op"):
        imgs = ig.find_all("img")
        if imgs:
            frag = soup.new_tag("p")
            for im in imgs:
                frag.append(im.extract())
            ig.replace_with(frag)
        else:
            ig.decompose()

    # Resolve images & remove invalid/icon images
    for img in list(soup.find_all("img")):
        src = _resolve_img_src(img, base_url)
        if not src:
            img.decompose()
            continue
        alt = img.get("alt", "")
        img.attrs = {"src": src}
        if alt:
            img["alt"] = alt

    # Normalize links
    for a in list(soup.find_all("a")):
        href = a.get("href", "").strip()
        if not href or href.startswith("javascript:"):
            a.unwrap()
            continue
        a.attrs = {"href": urllib.parse.urljoin(base_url, href), "target": "_blank"}

    # Clean attributes on allowed tags and unwrap purely presentational containers
    for el in list(soup.find_all(True)):
        if el.name in ("html", "body"):
            continue
        if el.name in UNWRAP_TAGS:
            el.unwrap()
        elif el.name not in ALLOWED_TAGS:
            el.unwrap()
        else:
            if el.name not in ("img", "a"):
                el.attrs = {}

    # Optional Discuz / prose polish: promote standalone <p><strong>Heading</strong></p> -> <h2>
    if promote_strong_p_to_h2:
        for p in list(soup.find_all("p")):
            txt = p.get_text("", strip=True)
            if not txt:
                p.decompose()
                continue
            strong = p.find(["strong", "b"])
            if strong and strong.get_text("", strip=True) == txt and len(txt) <= 45:
                h2 = soup.new_tag("h2")
                h2.string = txt
                p.replace_with(h2)
            elif txt.startswith("补充内容 ("):
                div = soup.new_tag("div")
                div["class"] = "supplement-hdr"
                div.string = txt
                p.replace_with(div)

    # Remove empty paragraphs (that contain neither text nor img/br)
    for p in list(soup.find_all("p")):
        if not p.get_text(strip=True) and not p.find(["img", "br"]):
            p.decompose()

    body = soup.body if soup.body else soup
    cleaned_html = "".join(str(c) for c in body.contents).strip()
    plain_len = len(body.get_text("", strip=True))
    return cleaned_html, plain_len


def _build_article_html(thread: ThreadData, replies: list[PostItem]) -> str:
    op = thread.posts[0]
    body_font = (
        '"FangSong", "STFangsong", "仿宋_GB2312", "Noto Serif CJK SC", "SimSun", serif'
        if thread.prefer_fangsong
        else '"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif'
    )
    p_indent = "2em" if thread.prefer_fangsong else "0"
    p_size = "15px" if thread.prefer_fangsong else "14px"

    reply_html = ""
    if replies:
        items = []
        for r in replies:
            badge = '<span class="lz-badge">楼主</span>' if r.is_lz else ""
            light = f'<span class="light-badge">亮了 ×{r.light_count}</span>' if r.light_count > 0 else ""
            q_html = ""
            if r.quote_text:
                q_lines = "<br>".join(html_lib.escape(x.strip()) for x in r.quote_text.splitlines() if x.strip())
                q_html = f'<div class="reply-quote">{q_lines}</div>'
            items.append(
                f'''<div class="reply-item">
                  <div class="reply-meta">
                    <span class="reply-floor">#{r.floor}</span>
                    <span class="reply-author">{html_lib.escape(r.author)}</span>{badge}{light}
                    <span class="reply-time">{html_lib.escape(r.time)}</span>
                  </div>
                  {q_html}
                  <div class="reply-body">{r.body_html}</div>
                </div>'''
            )
        reply_html = f'''
        <section class="replies-section">
          <div class="replies-title">附：讨论与回复（共 {len(replies)} 条）</div>
          {"".join(items)}
        </section>'''

    note_html = f'<div class="trunc-note">{html_lib.escape(thread.truncated_note)}</div>' if thread.truncated_note else ""

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="referrer" content="no-referrer">
<title>{html_lib.escape(thread.title)}</title>
<style>
  @page {{ size: A4; margin: 18mm 18mm 18mm 18mm; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 0; color: #1f2328; background: #fff;
    font-family: {body_font};
    font-size: {p_size}; line-height: 1.78;
    -webkit-print-color-adjust: exact; print-color-adjust: exact;
  }}
  .doc-header {{ border-bottom: 1.5px solid #2b4c7e; padding-bottom: 12px; margin-bottom: 20px; }}
  .doc-tag {{
    display: inline-block; font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    font-size: 11px; font-weight: 600; color: #2b4c7e; background: #eef3fa;
    padding: 2px 8px; border-radius: 3px; margin-bottom: 8px;
  }}
  h1 {{
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    font-size: 20.5px; font-weight: 700; line-height: 1.4; color: #111827; margin: 0 0 8px 0;
  }}
  .doc-meta {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; font-size: 11.5px; color: #57606a; }}
  .doc-meta span + span::before {{ content: " · "; color: #b0b8c1; margin: 0 4px; }}
  .doc-meta a {{ color: #2b4c7e; text-decoration: none; }}
  main h1, main h2, main h3, main h4 {{
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    color: #1e3a5f; break-after: avoid; page-break-after: avoid;
  }}
  main h2 {{
    font-size: 16px; font-weight: 700; margin: 20px 0 10px 0;
    padding-left: 10px; border-left: 3.5px solid #2b4c7e; line-height: 1.4;
  }}
  main h3 {{ font-size: 14.5px; font-weight: 700; margin: 16px 0 8px 0; }}
  main h4 {{ font-size: 13.5px; font-weight: 700; margin: 12px 0 6px 0; }}
  main p {{ margin: 0 0 10px 0; text-indent: {p_indent}; text-align: justify; orphans: 3; widows: 3; }}
  main blockquote {{
    margin: 10px 0; padding: 8px 12px; background: #f8fafc;
    border-left: 3.5px solid #94a3b8; color: #475569; font-size: 0.95em;
  }}
  main blockquote p {{ text-indent: 0; margin: 0 0 4px 0; }}
  main ul, main ol {{ margin: 6px 0 12px 0; padding-left: 22px; }}
  main li {{ margin-bottom: 4px; }}
  main pre {{
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 11.5px; line-height: 1.55; background: #f6f8fa; border: 1px solid #e2e8f0;
    border-radius: 5px; padding: 10px 12px; margin: 10px 0;
    white-space: pre-wrap; word-break: break-all; break-inside: avoid;
  }}
  main code {{
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 0.9em; background: #f1f5f9; padding: 1px 4px; border-radius: 3px;
  }}
  main pre code {{ background: transparent; padding: 0; font-size: inherit; }}
  main table {{
    width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 12.5px;
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif; break-inside: avoid;
  }}
  main th, main td {{ border: 1px solid #cbd5e1; padding: 6px 8px; text-align: left; }}
  main th {{ background: #f1f5f9; font-weight: 600; }}
  main img {{
    display: block; max-width: 100%; max-height: 200mm; height: auto;
    margin: 10px auto; border-radius: 4px; break-inside: avoid;
  }}
  main a {{ color: #2b4c7e; text-decoration: none; word-break: break-all; }}
  .supplement-hdr {{
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    font-weight: 700; color: #1e3a5f; margin: 10px 0 4px 0; font-size: 13px;
  }}
  .replies-section {{ margin-top: 24px; padding-top: 14px; border-top: 1px dashed #cbd5e1; }}
  .replies-title {{
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    font-size: 14px; font-weight: 700; color: #334155; margin: 0 0 12px 0; break-after: avoid;
  }}
  .reply-item {{
    background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 5px;
    padding: 10px 14px; margin-bottom: 10px; break-inside: avoid; page-break-inside: avoid;
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
  }}
  .reply-meta {{ font-size: 11.5px; color: #64748b; margin-bottom: 5px; }}
  .reply-floor {{ font-weight: 600; color: #2b4c7e; margin-right: 6px; }}
  .reply-author {{ font-weight: 600; color: #1e293b; }}
  .lz-badge {{
    display: inline-block; font-size: 10px; color: #fff; background: #2b4c7e;
    border-radius: 3px; padding: 0 5px; margin-left: 5px;
  }}
  .light-badge {{
    display: inline-block; font-size: 10px; color: #b45309; background: #fef3c7;
    border: 1px solid #fde68a; border-radius: 3px; padding: 0 5px; margin-left: 5px;
  }}
  .reply-time {{ margin-left: 10px; color: #94a3b8; }}
  .reply-quote {{
    font-size: 11.5px; color: #64748b; background: #eef2f6;
    border-left: 3px solid #94a3b8; padding: 6px 10px; margin: 6px 0 8px 0; line-height: 1.5;
  }}
  .reply-body {{ font-size: 13px; line-height: 1.65; color: #1f2328; }}
  .reply-body p {{ margin: 0 0 4px 0; text-indent: 0; }}
  .reply-body img {{ max-width: 100%; max-height: 120mm; display: block; margin: 6px 0; }}
  .trunc-note {{
    margin-top: 12px; font-family: "Microsoft YaHei", sans-serif;
    font-size: 11.5px; color: #64748b; text-align: center;
  }}
</style>
</head>
<body>
  <header class="doc-header">
    <div class="doc-tag">{html_lib.escape(thread.platform)} · {html_lib.escape(thread.forum_name)}</div>
    <h1>{html_lib.escape(thread.title)}</h1>
    <div class="doc-meta">
      <span>作者：{html_lib.escape(op.author)}</span>
      {f'<span>发布时间：{html_lib.escape(op.time)}</span>' if op.time else ''}
      <span>原文：<a href="{html_lib.escape(thread.source_url)}">{html_lib.escape(thread.domain)} ({html_lib.escape(thread.thread_id)})</a></span>
    </div>
  </header>
  <main>{op.body_html}</main>
  {reply_html}
  {note_html}
</body>
</html>'''


def _build_qa_html(thread: ThreadData, replies: list[PostItem]) -> str:
    op = thread.posts[0]
    cards = []
    for r in replies:
        lz_cls = " reply-lz" if r.is_lz else ""
        lz_badge = '<span class="lz-badge">楼主</span>' if r.is_lz else ""
        rec_badge = '<span class="rec-badge">推荐</span>' if r.is_rec else ""
        light_badge = f'<span class="light-badge">亮了 ×{r.light_count}</span>' if r.light_count > 0 else ""
        pstatus = f'<div class="pstatus-note">{html_lib.escape(r.pstatus)}</div>' if r.pstatus else ""
        q_html = ""
        if r.quote_text:
            q_lines = "<br>".join(html_lib.escape(x.strip()) for x in r.quote_text.splitlines() if x.strip())
            q_html = f'<div class="reply-quote">{q_lines}</div>'
        cards.append(
            f'''<article class="reply-card{lz_cls}">
              <div class="post-meta">
                <span class="floor-badge">#{r.floor}</span>
                <span class="author-name">{html_lib.escape(r.author)}</span>{lz_badge}{rec_badge}{light_badge}
                <span class="post-time">{html_lib.escape(r.time)}</span>
              </div>
              {pstatus}{q_html}
              <div class="reply-body">{r.body_html}</div>
            </article>'''
        )

    note_html = f'<div class="trunc-note">{html_lib.escape(thread.truncated_note)}</div>' if thread.truncated_note else ""

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="referrer" content="no-referrer">
<title>{html_lib.escape(thread.title)}</title>
<style>
  @page {{ size: A4; margin: 16mm 16mm 18mm 16mm; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 0; color: #1f2328; background: #fff;
    font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
    font-size: 13px; line-height: 1.65;
    -webkit-print-color-adjust: exact; print-color-adjust: exact;
  }}
  .doc-header {{ border-bottom: 2px solid #2b4c7e; padding-bottom: 12px; margin-bottom: 16px; }}
  .doc-tag {{
    display: inline-block; font-size: 11px; font-weight: 600; color: #2b4c7e;
    background: #eef3fa; padding: 2px 8px; border-radius: 3px; margin-bottom: 8px;
  }}
  h1 {{ font-size: 19.5px; font-weight: 700; color: #111827; margin: 0 0 8px 0; line-height: 1.35; }}
  .doc-meta {{ font-size: 11.5px; color: #57606a; }}
  .doc-meta span + span::before {{ content: " · "; color: #b0b8c1; margin: 0 4px; }}
  .doc-meta a {{ color: #2b4c7e; text-decoration: none; }}
  .op-card {{
    background: #f8fafc; border: 1.5px solid #cbd5e1; border-left: 4px solid #2b4c7e;
    border-radius: 6px; padding: 12px 15px; margin-bottom: 16px;
  }}
  .section-divider {{
    font-size: 13px; font-weight: 700; color: #334155; margin: 14px 0 10px 0;
    padding-bottom: 4px; border-bottom: 1px solid #e2e8f0; break-after: avoid;
  }}
  .reply-card {{
    background: #fff; border: 1px solid #e2e8f0; border-radius: 5px;
    padding: 9px 13px; margin-bottom: 8px; break-inside: avoid; page-break-inside: avoid;
  }}
  .reply-lz {{ background: #f9fbff; border-color: #cbd9ee; border-left: 3.5px solid #2b4c7e; }}
  .post-meta {{
    font-size: 11.5px; color: #64748b; margin-bottom: 5px;
    display: flex; align-items: center; flex-wrap: wrap; gap: 5px;
  }}
  .floor-badge {{ font-weight: 700; color: #475569; }}
  .op-floor {{ color: #2b4c7e; }}
  .author-name {{ font-weight: 600; color: #1e293b; }}
  .lz-badge {{
    display: inline-block; font-size: 10px; line-height: 1.4; color: #fff;
    background: #2b4c7e; border-radius: 3px; padding: 0 5px;
  }}
  .rec-badge {{
    display: inline-block; font-size: 10px; line-height: 1.4; color: #9a3412;
    background: #ffedd5; border: 1px solid #fed7aa; border-radius: 3px; padding: 0 5px;
  }}
  .light-badge {{
    display: inline-block; font-size: 10px; line-height: 1.4; color: #b45309;
    background: #fef3c7; border: 1px solid #fde68a; border-radius: 3px; padding: 0 5px;
  }}
  .post-time {{ color: #94a3b8; margin-left: auto; }}
  .pstatus-note {{ font-size: 11px; color: #94a3b8; font-style: italic; margin-bottom: 4px; }}
  .reply-quote {{
    font-size: 11.5px; color: #64748b; background: #f1f5f9; border-left: 3px solid #94a3b8;
    padding: 5px 9px; margin: 4px 0 6px 0; line-height: 1.5; border-radius: 0 3px 3px 0;
  }}
  .op-body p, .reply-body p {{ margin: 0 0 6px 0; }}
  .op-body p:last-child, .reply-body p:last-child {{ margin-bottom: 0; }}
  .op-body h2, .op-body h3, .reply-body h2, .reply-body h3 {{
    font-size: 14px; font-weight: 700; color: #1e3a5f; margin: 10px 0 4px 0;
  }}
  .op-body pre, .reply-body pre {{
    font-family: "Cascadia Code", "Consolas", monospace; font-size: 11.5px; line-height: 1.5;
    background: #f6f8fa; border: 1px solid #e2e8f0; border-radius: 4px; padding: 8px 10px;
    white-space: pre-wrap; word-break: break-all; margin: 6px 0;
  }}
  .op-body table, .reply-body table {{
    width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 12px;
  }}
  .op-body th, .op-body td, .reply-body th, .reply-body td {{
    border: 1px solid #cbd5e1; padding: 4px 7px;
  }}
  .op-body th, .reply-body th {{ background: #f1f5f9; }}
  .op-body img, .reply-body img {{
    display: block; max-width: 100%; max-height: 140mm; height: auto;
    margin: 6px auto; border-radius: 4px;
  }}
  .op-body a, .reply-body a {{ color: #2b4c7e; text-decoration: none; word-break: break-all; }}
  .supplement-hdr {{ font-weight: 700; color: #1e3a5f; margin: 6px 0 2px 0; font-size: 12.5px; }}
  .trunc-note {{ margin-top: 12px; font-size: 11.5px; color: #64748b; text-align: center; }}
</style>
</head>
<body>
  <header class="doc-header">
    <div class="doc-tag">{html_lib.escape(thread.platform)} · {html_lib.escape(thread.forum_name)}</div>
    <h1>{html_lib.escape(thread.title)}</h1>
    <div class="doc-meta">
      <span>作者：{html_lib.escape(op.author)}</span>
      {f'<span>发布时间：{html_lib.escape(op.time)}</span>' if op.time else ''}
      <span>收录楼层：共 {len(replies) + 1} 楼（{thread.total_pages} 页）</span>
      <span>原文：<a href="{html_lib.escape(thread.source_url)}">{html_lib.escape(thread.domain)} ({html_lib.escape(thread.thread_id)})</a></span>
    </div>
  </header>
  <section class="op-card">
    <div class="post-meta">
      <span class="floor-badge op-floor">#1 主帖</span>
      <span class="author-name">{html_lib.escape(op.author)}</span>
      <span class="lz-badge">楼主</span>
      <span class="post-time">{html_lib.escape(op.time)}</span>
    </div>
    <div class="op-body">{op.body_html}</div>
  </section>
  {f'<div class="section-divider">讨论交流记录（共 {len(replies)} 条）</div>' if replies else ''}
  <main>{"".join(cards)}</main>
  {note_html}
</body>
</html>'''


def _download_single_image(img_url: str, dest_dir: str, referer: str) -> Optional[str]:
    """Download an image locally trying platform Referer first, then no-Referer."""
    url_hash = hashlib.md5(img_url.encode("utf-8")).hexdigest()[:16]
    # For WeChat mmbiz, no-referrer works reliably; for others try source Referer first
    refs = ["", referer] if "mmbiz.qpic.cn" in img_url else [referer, ""]
    for ref in refs:
        headers = {"User-Agent": USER_AGENT}
        if ref:
            headers["Referer"] = ref
        try:
            req = urllib.request.Request(img_url, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = resp.read()
                if not data:
                    continue
                ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                ext = mimetypes.guess_extension(ct) or ""
                if ext == ".jpe":
                    ext = ".jpg"
                if not ext:
                    path_ext = os.path.splitext(urllib.parse.urlparse(img_url).path)[1].lower()
                    ext = path_ext if path_ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg") else ".png"
                local_path = os.path.join(dest_dir, f"{url_hash}{ext}")
                with open(local_path, "wb") as f:
                    f.write(data)
                return f"file:///{local_path.replace(os.sep, '/')}"
        except Exception:
            continue
    return None


def _localize_thread_images(posts: list[PostItem], referer: str, assets_dir: str) -> None:
    """Download all external <img> URLs concurrently and rewrite src to local file:// URIs."""
    urls: list[str] = []
    for post in posts:
        if not post.body_html or "<img" not in post.body_html:
            continue
        soup = BeautifulSoup(post.body_html, "lxml")
        for im in soup.find_all("img"):
            src = im.get("src")
            if src and src.startswith(("http://", "https://")) and src not in urls:
                urls.append(src)
    if not urls:
        return

    os.makedirs(assets_dir, exist_ok=True)
    url_to_local: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_download_single_image, u, assets_dir, referer): u for u in urls}
        for fut, orig_url in futures.items():
            local_uri = fut.result()
            if local_uri:
                url_to_local[orig_url] = local_uri

    if not url_to_local:
        return
    for post in posts:
        if not post.body_html or "<img" not in post.body_html:
            continue
        soup = BeautifulSoup(post.body_html, "lxml")
        changed = False
        for im in soup.find_all("img"):
            src = im.get("src")
            if src in url_to_local:
                im["src"] = url_to_local[src]
                changed = True
        if changed:
            body = soup.body if soup.body else soup
            post.body_html = "".join(str(c) for c in body.contents).strip()


def _launch_browser(playwright):
    for channel in ("msedge", "chrome", None):
        try:
            return playwright.chromium.launch(channel=channel, headless=True) if channel else playwright.chromium.launch(headless=True)
        except Exception:
            continue
    raise RuntimeError("未找到可用的 Edge/Chrome/Chromium 浏览器实例。")


def render_thread_pdf(
    thread: ThreadData,
    pdf_path: str,
    mode: str = "auto",
    replies_scope: str = "all",
    html_out: Optional[str] = None,
    verify_dir: Optional[str] = None,
) -> dict:
    op = thread.posts[0]
    raw_replies = thread.posts[1:]
    if replies_scope == "none":
        replies = []
    elif replies_scope == "lz-only":
        replies = [r for r in raw_replies if r.is_lz]
    else:
        replies = raw_replies

    # Localize images so CDN Referer/hotlink differences never cause broken icons in PDF
    assets_dir = tempfile.mkdtemp(prefix="forum_pdf_assets_")
    try:
        _localize_thread_images([op] + replies, thread.source_url, assets_dir)

        chosen_mode = mode
        if chosen_mode == "auto":
            chosen_mode = "article" if (op.text_len >= 600 and len(replies) <= 20) else "qa"

        if chosen_mode == "article":
            html_str = _build_article_html(thread, replies)
            margin = {"top": "18mm", "bottom": "18mm", "left": "18mm", "right": "18mm"}
        else:
            html_str = _build_qa_html(thread, replies)
            margin = {"top": "16mm", "bottom": "18mm", "left": "16mm", "right": "16mm"}

        os.makedirs(os.path.dirname(os.path.abspath(pdf_path)), exist_ok=True)
        html_path = html_out or (os.path.splitext(pdf_path)[0] + ".tmp.html")
        os.makedirs(os.path.dirname(os.path.abspath(html_path)), exist_ok=True)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_str)

        with sync_playwright() as p:
            browser = _launch_browser(p)
            page = browser.new_page()
            page.goto(f"file:///{os.path.abspath(html_path).replace(os.sep, '/')}", wait_until="load", timeout=45000)
            page.evaluate("""async () => {
                const imgs = Array.from(document.images);
                await Promise.all(imgs.map(img => {
                    if (img.complete) return Promise.resolve();
                    return new Promise(resolve => {
                        const t = setTimeout(resolve, 6000);
                        img.addEventListener('load', () => { clearTimeout(t); resolve(); }, { once: true });
                        img.addEventListener('error', () => { clearTimeout(t); resolve(); }, { once: true });
                    });
                }));
            }""")
            page.pdf(
                path=pdf_path,
                format="A4",
                print_background=True,
                display_header_footer=True,
                header_template="<div></div>",
                footer_template=(
                    '<div style="font-size:9px; color:#94a3b8; width:100%; text-align:center; '
                    'font-family:Microsoft YaHei,sans-serif;">第 <span class="pageNumber"></span> 页 / 共 <span class="totalPages"></span> 页</div>'
                ),
                margin=margin,
            )
            browser.close()

        if not html_out and os.path.exists(html_path):
            os.remove(html_path)
    finally:
        shutil.rmtree(assets_dir, ignore_errors=True)

    info = {
        "platform": thread.platform,
        "title": thread.title,
        "mode": chosen_mode,
        "total_pages_fetched": thread.total_pages,
        "posts_included": len(replies) + 1,
        "pdf_path": pdf_path,
        "pdf_bytes": os.path.getsize(pdf_path),
    }

    if verify_dir:
        import pymupdf
        os.makedirs(verify_dir, exist_ok=True)
        doc = pymupdf.open(pdf_path)
        info["pdf_pages"] = len(doc)
        info["page_chars"] = []
        base_tag = sanitize_filename(thread.thread_id)
        for i, pg in enumerate(doc):
            chars = len(pg.get_text("text"))
            info["page_chars"].append(chars)
            if i in (0, len(doc) - 1):
                out_png = os.path.join(verify_dir, f"{base_tag}_p{i+1}.png")
                pg.get_pixmap(dpi=130).save(out_png)
        doc.close()

    return info
