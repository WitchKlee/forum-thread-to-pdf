#!/usr/bin/env python3
"""
Fetch a Discuz! forum thread (e.g., chongbuluo.com) across all pages,
clean & deduplicate posts, typeset as clean HTML, and render to A4 PDF.
"""

import argparse
import html as html_lib
import os
import re
import sys
import urllib.parse
import urllib.request
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def fetch_html(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_tid_and_base(url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}/"
    qs = urllib.parse.parse_qs(parsed.query)
    if "tid" in qs and qs["tid"]:
        return qs["tid"][0], base
    m = re.search(r"thread-(\d+)-\d+-\d+\.html", url)
    if m:
        return m.group(1), base
    raise ValueError(f"Cannot extract tid from URL: {url}")


def detect_total_pages(html: str, tid: str) -> int:
    m_span = re.search(r'title="共\s*(\d+)\s*页"', html)
    pages = [1]
    if m_span:
        pages.append(int(m_span.group(1)))
    for p in re.findall(rf"thread-{re.escape(tid)}-(\d+)-\d+\.html", html):
        pages.append(int(p))
    for p in re.findall(rf"tid={re.escape(tid)}[^\"']*?page=(\d+)", html):
        pages.append(int(p))
    return max(pages)


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name).strip()
    return cleaned[:120] or "forum_thread"


def extract_images_from_msg(msg_el, base_url: str) -> list[str]:
    urls = []
    for img in msg_el.find_all("img"):
        src = img.get("file") or img.get("zoomfile") or img.get("src") or ""
        if not src or src.startswith("data:"):
            continue
        # Skip Discuz built-in smilies / common icons
        if "static/image/smiley/" in src or "static/image/common/" in src:
            continue
        abs_url = urllib.parse.urljoin(base_url, src)
        if abs_url not in urls:
            urls.append(abs_url)
    return urls


def parse_post_block(post_div, base_url: str) -> dict:
    pid_str = post_div.get("id", "post_0")
    pid_num = int(pid_str.split("_")[1]) if "_" in pid_str else 0

    floor_el = post_div.select_one("strong a[id^='postnum']")
    floor_txt = floor_el.get_text(strip=True) if floor_el else "?"
    is_rec = floor_txt == "推荐"

    author_el = post_div.select_one(".pi .authi a")
    author = author_el.get_text(strip=True) if author_el else "匿名"
    authi_el = post_div.select_one(".pi .authi")
    is_lz = bool(authi_el and "楼主" in authi_el.get_text())

    time_el = post_div.select_one("em[id^='authorposton']")
    span = time_el.find("span") if time_el else None
    ptime = (
        span.get("title")
        if (span and span.get("title"))
        else (time_el.get_text(" ", strip=True).replace("发表于 ", "") if time_el else "")
    )

    msg = post_div.select_one("td.t_f")
    pstatus_text = None
    quote_text = None
    images = []
    blocks = []  # list of (kind, text) where kind in {"heading", "supplement", "paragraph", "gap"}

    if msg:
        images = extract_images_from_msg(msg, base_url)
        msg_copy = BeautifulSoup(str(msg), "lxml")

        pstatus_el = msg_copy.select_one("i.pstatus")
        if pstatus_el:
            pstatus_text = pstatus_el.get_text(" ", strip=True)
            pstatus_el.decompose()

        q_el = msg_copy.select_one(".quote blockquote")
        if q_el:
            quote_text = q_el.get_text("\n", strip=True)
            for q in msg_copy.select(".quote"):
                q.decompose()

        td_el = msg_copy.select_one("td.t_f") or msg_copy
        ps = td_el.find_all("p")
        if len(ps) >= 3:
            for p in ps:
                txt = p.get_text("", strip=True)
                if not txt:
                    continue
                strong = p.find("strong")
                if strong and strong.get_text("", strip=True) == txt:
                    blocks.append(("heading", txt))
                elif txt.startswith("补充内容 ("):
                    blocks.append(("supplement", txt))
                else:
                    blocks.append(("paragraph", txt))
        else:
            for br in td_el.find_all("br"):
                br.replace_with("\n")
            prev_blank = False
            for raw in td_el.get_text().splitlines():
                ln = raw.strip()
                if not ln:
                    if not prev_blank and blocks:
                        blocks.append(("gap", ""))
                    prev_blank = True
                else:
                    if ln.startswith("补充内容 ("):
                        blocks.append(("supplement", ln))
                    else:
                        blocks.append(("paragraph", ln))
                    prev_blank = False
            while blocks and blocks[-1][0] == "gap":
                blocks.pop()

    plain_len = sum(len(t) for k, t in blocks if k != "gap")
    return {
        "pid_num": pid_num,
        "is_rec": is_rec,
        "author": author,
        "is_lz": is_lz,
        "time": ptime,
        "pstatus": pstatus_text,
        "quote": quote_text,
        "blocks": blocks,
        "images": images,
        "text_len": plain_len,
    }


def collect_thread(url: str) -> dict:
    tid, base_url = parse_tid_and_base(url)
    page1_url = urllib.parse.urljoin(base_url, f"thread-{tid}-1-1.html")
    html1 = fetch_html(page1_url)
    total_pages = detect_total_pages(html1, tid)

    posts_map = {}
    thread_title = None
    forum_name = None

    for pg in range(1, total_pages + 1):
        html = html1 if pg == 1 else fetch_html(urllib.parse.urljoin(base_url, f"thread-{tid}-{pg}-1.html"))
        soup = BeautifulSoup(html, "lxml")

        if thread_title is None:
            t_el = soup.find(id="thread_subject")
            thread_title = t_el.get_text(strip=True) if t_el else f"Thread {tid}"
            pt = soup.select("#pt .z a")
            if pt:
                forum_name = pt[-1].get_text(strip=True)

        for p_div in soup.select("div#postlist > div[id^='post_']"):
            parsed = parse_post_block(p_div, base_url)
            pid = parsed["pid_num"]
            if pid not in posts_map or (posts_map[pid]["is_rec"] and not parsed["is_rec"]):
                posts_map[pid] = parsed

    sorted_posts = [posts_map[k] for k in sorted(posts_map.keys())]
    if not sorted_posts:
        raise RuntimeError("No posts parsed from thread. It may require login or anti-bot verification.")

    op_author = sorted_posts[0]["author"]
    for idx, p in enumerate(sorted_posts, start=1):
        p["seq_floor"] = idx
        if p["author"] == op_author:
            p["is_lz"] = True

    return {
        "tid": tid,
        "base_url": base_url,
        "source_url": f"{base_url}forum.php?mod=viewthread&tid={tid}",
        "domain": urllib.parse.urlparse(base_url).netloc,
        "title": thread_title,
        "forum_name": forum_name or "论坛",
        "total_pages": total_pages,
        "posts": sorted_posts,
    }


def render_blocks_html(blocks: list[tuple[str, str]], paragraph_class: str = "body-p") -> str:
    out = []
    for kind, txt in blocks:
        esc = html_lib.escape(txt)
        if kind == "heading":
            out.append(f"<h2>{esc}</h2>")
        elif kind == "supplement":
            out.append(f'<div class="supplement-hdr">{esc}</div>')
        elif kind == "gap":
            out.append('<div class="line-gap"></div>')
        else:
            out.append(f'<p class="{paragraph_class}">{esc}</p>')
    return "\n".join(out)


def render_images_html(images: list[str]) -> str:
    if not images:
        return ""
    imgs = [f'<div class="post-img-wrap"><img src="{html_lib.escape(u)}" alt="post image" /></div>' for u in images]
    return "\n".join(imgs)


def build_article_html(thread: dict, replies: list[dict]) -> str:
    op = thread["posts"][0]
    op_body = render_blocks_html(op["blocks"], paragraph_class="body-p")
    op_imgs = render_images_html(op["images"])

    reply_html = ""
    if replies:
        items = []
        for r in replies:
            badge = '<span class="lz-badge">楼主</span>' if r["is_lz"] else ""
            q_html = ""
            if r["quote"]:
                q_lines = "<br>".join(html_lib.escape(x.strip()) for x in r["quote"].splitlines() if x.strip())
                q_html = f'<div class="reply-quote">{q_lines}</div>'
            body = render_blocks_html(r["blocks"], paragraph_class="reply-line")
            imgs = render_images_html(r["images"])
            items.append(
                f'''<div class="reply-item">
                  <div class="reply-meta">
                    <span class="reply-floor">#{r["seq_floor"]}</span>
                    <span class="reply-author">{html_lib.escape(r["author"])}</span>{badge}
                    <span class="reply-time">{html_lib.escape(r["time"])}</span>
                  </div>
                  {q_html}
                  <div class="reply-body">{body}{imgs}</div>
                </div>'''
            )
        reply_html = f'''
        <section class="replies-section">
          <div class="replies-title">附：原帖后续讨论（共 {len(replies)} 条回复）</div>
          {"".join(items)}
        </section>'''

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{html_lib.escape(thread["title"])}</title>
<style>
  @page {{ size: A4; margin: 18mm 20mm 18mm 20mm; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 0; color: #1f2328; background: #fff;
    font-family: "FangSong", "STFangsong", "仿宋_GB2312", "Noto Serif CJK SC", "SimSun", serif;
    font-size: 15px; line-height: 1.78;
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
    font-size: 21px; font-weight: 700; line-height: 1.4; color: #111827; margin: 0 0 8px 0;
  }}
  .doc-meta {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; font-size: 12px; color: #57606a; }}
  .doc-meta span + span::before {{ content: " · "; color: #b0b8c1; margin: 0 4px; }}
  .doc-meta a {{ color: #2b4c7e; text-decoration: none; }}
  h2 {{
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    font-size: 16px; font-weight: 700; color: #1e3a5f; margin: 20px 0 10px 0;
    padding-left: 10px; border-left: 3.5px solid #2b4c7e; line-height: 1.4;
    break-after: avoid; page-break-after: avoid;
  }}
  p.body-p {{ margin: 0 0 10px 0; text-indent: 2em; text-align: justify; orphans: 3; widows: 3; }}
  p.reply-line {{ margin: 0; }}
  .line-gap {{ height: 6px; }}
  .supplement-hdr {{
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    font-weight: 700; color: #1e3a5f; margin: 10px 0 4px 0; font-size: 13px;
  }}
  .post-img-wrap {{ margin: 10px 0; text-align: center; break-inside: avoid; }}
  .post-img-wrap img {{ max-width: 100%; max-height: 220mm; border-radius: 4px; }}
  .replies-section {{ margin-top: 26px; padding-top: 14px; border-top: 1px dashed #cbd5e1; }}
  .replies-title {{
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    font-size: 14px; font-weight: 700; color: #334155; margin: 0 0 12px 0; break-after: avoid;
  }}
  .reply-item {{
    background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 5px;
    padding: 10px 14px; margin-bottom: 10px; break-inside: avoid; page-break-inside: avoid;
  }}
  .reply-meta {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; font-size: 12px; color: #64748b; margin-bottom: 5px; }}
  .reply-floor {{ font-weight: 600; color: #2b4c7e; margin-right: 6px; }}
  .reply-author {{ font-weight: 600; color: #1e293b; }}
  .lz-badge {{
    display: inline-block; font-size: 10px; color: #fff; background: #2b4c7e;
    border-radius: 3px; padding: 0 5px; margin-left: 5px; vertical-align: 1px;
  }}
  .reply-time {{ margin-left: 10px; color: #94a3b8; }}
  .reply-quote {{
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif; font-size: 12px; color: #64748b;
    background: #eef2f6; border-left: 3px solid #94a3b8; padding: 6px 10px; margin: 6px 0 8px 0; line-height: 1.5;
  }}
  .reply-body {{ font-size: 14.5px; line-height: 1.7; color: #1f2328; }}
</style>
</head>
<body>
  <header class="doc-header">
    <div class="doc-tag">{html_lib.escape(thread["forum_name"])}</div>
    <h1>{html_lib.escape(thread["title"])}</h1>
    <div class="doc-meta">
      <span>作者：{html_lib.escape(op["author"])}</span>
      <span>发布时间：{html_lib.escape(op["time"])}</span>
      <span>原文：<a href="{html_lib.escape(thread["source_url"])}">{html_lib.escape(thread["domain"])} (tid={thread["tid"]})</a></span>
    </div>
  </header>
  <main>{op_body}{op_imgs}</main>
  {reply_html}
</body>
</html>'''


def build_qa_html(thread: dict, replies: list[dict]) -> str:
    op = thread["posts"][0]
    op_body = render_blocks_html(op["blocks"], paragraph_class="post-line")
    op_imgs = render_images_html(op["images"])

    cards = []
    for r in replies:
        lz_cls = " reply-lz" if r["is_lz"] else ""
        lz_badge = '<span class="lz-badge">楼主</span>' if r["is_lz"] else ""
        rec_badge = '<span class="rec-badge">推荐</span>' if r["is_rec"] else ""
        pstatus = f'<div class="pstatus-note">{html_lib.escape(r["pstatus"])}</div>' if r["pstatus"] else ""
        q_html = ""
        if r["quote"]:
            q_lines = "<br>".join(html_lib.escape(x.strip()) for x in r["quote"].splitlines() if x.strip())
            q_html = f'<div class="reply-quote">{q_lines}</div>'
        body = render_blocks_html(r["blocks"], paragraph_class="post-line")
        imgs = render_images_html(r["images"])
        cards.append(
            f'''<article class="reply-card{lz_cls}">
              <div class="post-meta">
                <span class="floor-badge">#{r["seq_floor"]}</span>
                <span class="author-name">{html_lib.escape(r["author"])}</span>{lz_badge}{rec_badge}
                <span class="post-time">{html_lib.escape(r["time"])}</span>
              </div>
              {pstatus}{q_html}
              <div class="reply-body">{body}{imgs}</div>
            </article>'''
        )

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{html_lib.escape(thread["title"])}</title>
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
  h1 {{ font-size: 20px; font-weight: 700; color: #111827; margin: 0 0 8px 0; line-height: 1.35; }}
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
  .post-time {{ color: #94a3b8; margin-left: auto; }}
  .pstatus-note {{ font-size: 11px; color: #94a3b8; font-style: italic; margin-bottom: 4px; }}
  .reply-quote {{
    font-size: 11.5px; color: #64748b; background: #f1f5f9; border-left: 3px solid #94a3b8;
    padding: 5px 9px; margin: 4px 0 6px 0; line-height: 1.5; border-radius: 0 3px 3px 0;
  }}
  p.post-line {{ margin: 0; }}
  h2 {{ font-size: 14.5px; font-weight: 700; color: #1e3a5f; margin: 10px 0 4px 0; }}
  .line-gap {{ height: 6px; }}
  .supplement-hdr {{ font-weight: 700; color: #1e3a5f; margin: 6px 0 2px 0; font-size: 12.5px; }}
  .post-img-wrap {{ margin: 8px 0; text-align: center; break-inside: avoid; }}
  .post-img-wrap img {{ max-width: 100%; max-height: 180mm; border-radius: 4px; }}
</style>
</head>
<body>
  <header class="doc-header">
    <div class="doc-tag">{html_lib.escape(thread["forum_name"])}</div>
    <h1>{html_lib.escape(thread["title"])}</h1>
    <div class="doc-meta">
      <span>作者：{html_lib.escape(op["author"])}</span>
      <span>发布时间：{html_lib.escape(op["time"])}</span>
      <span>收录楼层：共 {len(replies) + 1} 楼（{thread["total_pages"]} 页，已去重）</span>
      <span>原文：<a href="{html_lib.escape(thread["source_url"])}">{html_lib.escape(thread["domain"])} (tid={thread["tid"]})</a></span>
    </div>
  </header>
  <section class="op-card">
    <div class="post-meta">
      <span class="floor-badge op-floor">#1 主帖</span>
      <span class="author-name">{html_lib.escape(op["author"])}</span>
      <span class="lz-badge">楼主</span>
      <span class="post-time">{html_lib.escape(op["time"])}</span>
    </div>
    <div class="op-body">{op_body}{op_imgs}</div>
  </section>
  {f'<div class="section-divider">讨论交流全记录（共 {len(replies)} 条）</div>' if replies else ''}
  <main>{"".join(cards)}</main>
</body>
</html>'''


def render_pdf(html_str: str, html_path: str, pdf_path: str, margin_mm: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(html_path)), exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(pdf_path)), exist_ok=True)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_str)

    with sync_playwright() as p:
        browser = None
        for channel in ("msedge", "chrome", None):
            try:
                browser = p.chromium.launch(channel=channel, headless=True) if channel else p.chromium.launch(headless=True)
                break
            except Exception:
                continue
        if browser is None:
            raise RuntimeError("No Chromium/Edge/Chrome browser available for Playwright.")
        page = browser.new_page()
        page.goto(f"file:///{os.path.abspath(html_path).replace(os.sep, '/')}", wait_until="networkidle")
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
            margin=margin_mm,
        )
        browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert a Discuz forum thread to a cleanly typeset PDF.")
    parser.add_argument("url", help="Thread URL (e.g. https://www.chongbuluo.com/forum.php?mod=viewthread&tid=25377)")
    parser.add_argument("-o", "--output", help="Output PDF path (defaults to <title>.pdf in current directory)")
    parser.add_argument("--mode", choices=["auto", "article", "qa"], default="auto", help="Layout mode")
    parser.add_argument("--replies", choices=["all", "lz-only", "none"], default="all", help="Which replies to include")
    parser.add_argument("--html-out", help="Optional path to save intermediate HTML")
    parser.add_argument("--verify-dir", help="Optional directory to dump page PNG previews via PyMuPDF")
    args = parser.parse_args()

    thread = collect_thread(args.url)
    op = thread["posts"][0]
    raw_replies = thread["posts"][1:]

    if args.replies == "none":
        replies = []
    elif args.replies == "lz-only":
        replies = [r for r in raw_replies if r["is_lz"]]
    else:
        replies = raw_replies

    mode = args.mode
    if mode == "auto":
        mode = "article" if (op["text_len"] >= 1000 and len(replies) <= 15) else "qa"

    pdf_path = args.output or os.path.join(os.getcwd(), f"{sanitize_filename(thread['title'])}.pdf")
    html_path = args.html_out or os.path.splitext(pdf_path)[0] + ".tmp.html"

    if mode == "article":
        html_str = build_article_html(thread, replies)
        margin = {"top": "18mm", "bottom": "18mm", "left": "20mm", "right": "20mm"}
    else:
        html_str = build_qa_html(thread, replies)
        margin = {"top": "16mm", "bottom": "18mm", "left": "16mm", "right": "16mm"}

    render_pdf(html_str, html_path, pdf_path, margin)
    if not args.html_out and os.path.exists(html_path):
        os.remove(html_path)

    print(f"Title: {thread['title']}")
    print(f"Mode: {mode} | Pages fetched: {thread['total_pages']} | Unique posts: {len(thread['posts'])} | Included replies: {len(replies)}")
    print(f"Saved PDF: {pdf_path} ({os.path.getsize(pdf_path)} bytes)")

    if args.verify_dir:
        import pymupdf
        os.makedirs(args.verify_dir, exist_ok=True)
        doc = pymupdf.open(pdf_path)
        print(f"PDF page count: {len(doc)}")
        for i, page in enumerate(doc):
            txt = page.get_text("text")
            print(f"  Page {i+1}: chars={len(txt)}")
            if i in (0, len(doc) - 1):
                img_path = os.path.join(args.verify_dir, f"verify_p{i+1}.png")
                page.get_pixmap(dpi=130).save(img_path)
                print(f"    Preview: {img_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
