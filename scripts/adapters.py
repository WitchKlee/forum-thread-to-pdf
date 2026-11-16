"""
Platform-specific extractors for mainstream Chinese forums & content communities.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from core import (
    AccessBlockedError,
    PostItem,
    ThreadData,
    UnsupportedPlatformError,
    _launch_browser,
    clean_rich_html,
    fetch_json,
    fetch_text,
    format_timestamp,
)


def _extract_discuz(url: str, max_pages: int = 10) -> ThreadData:
    parsed = urllib.parse.urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}/"
    qs = urllib.parse.parse_qs(parsed.query)
    tid = qs["tid"][0] if qs.get("tid") else None
    if not tid:
        m = re.search(r"thread-(\d+)-\d+-\d+\.html", url)
        if m:
            tid = m.group(1)
    if not tid:
        raise UnsupportedPlatformError(f"无法从 Discuz 链接提取 tid：{url}")

    page1_url = urllib.parse.urljoin(base_url, f"thread-{tid}-1-1.html")
    html1 = fetch_text(page1_url)

    pages = [1]
    m_span = re.search(r'title="共\s*(\d+)\s*页"', html1)
    if m_span:
        pages.append(int(m_span.group(1)))
    for p in re.findall(rf"thread-{re.escape(tid)}-(\d+)-\d+\.html", html1):
        pages.append(int(p))
    for p in re.findall(rf"tid={re.escape(tid)}[^\"']*?page=(\d+)", html1):
        pages.append(int(p))
    total_pages = max(pages)
    fetch_limit = total_pages if max_pages <= 0 else min(total_pages, max_pages)

    posts_map: dict[int, tuple[bool, PostItem]] = {}
    thread_title = None
    forum_name = None

    for pg in range(1, fetch_limit + 1):
        html = html1 if pg == 1 else fetch_text(urllib.parse.urljoin(base_url, f"thread-{tid}-{pg}-1.html"))
        soup = BeautifulSoup(html, "lxml")

        if thread_title is None:
            t_el = soup.find(id="thread_subject")
            thread_title = t_el.get_text(strip=True) if t_el else f"Discuz Thread {tid}"
            pt = soup.select("#pt .z a")
            if pt:
                forum_name = pt[-1].get_text(strip=True)

        for p_div in soup.select("div#postlist > div[id^='post_']"):
            pid_str = p_div.get("id", "post_0")
            pid_num = int(pid_str.split("_")[1]) if "_" in pid_str else 0
            floor_el = p_div.select_one("strong a[id^='postnum']")
            floor_txt = floor_el.get_text(strip=True) if floor_el else "?"
            is_rec = floor_txt == "推荐"

            if pid_num in posts_map and not (posts_map[pid_num][0] and not is_rec):
                continue

            author_el = p_div.select_one(".pi .authi a")
            author = author_el.get_text(strip=True) if author_el else "匿名"
            authi_el = p_div.select_one(".pi .authi")
            is_lz = bool(authi_el and "楼主" in authi_el.get_text())

            time_el = p_div.select_one("em[id^='authorposton']")
            span = time_el.find("span") if time_el else None
            ptime = (
                span.get("title")
                if (span and span.get("title"))
                else (time_el.get_text(" ", strip=True).replace("发表于 ", "") if time_el else "")
            )

            msg = p_div.select_one("td.t_f")
            pstatus_text = None
            quote_text = None
            body_html = ""
            plain_len = 0
            if msg:
                pstatus_el = msg.select_one("i.pstatus")
                if pstatus_el:
                    pstatus_text = pstatus_el.get_text(" ", strip=True)
                q_el = msg.select_one(".quote blockquote")
                if q_el:
                    quote_text = q_el.get_text("\n", strip=True)
                body_html, plain_len = clean_rich_html(str(msg), base_url, promote_strong_p_to_h2=True)

            item = PostItem(
                floor=0,
                author=author,
                time=ptime,
                body_html=body_html,
                is_lz=is_lz,
                is_rec=is_rec,
                quote_text=quote_text,
                pstatus=pstatus_text,
                text_len=plain_len,
            )
            posts_map[pid_num] = (is_rec, item)

    sorted_posts = [posts_map[k][1] for k in sorted(posts_map.keys())]
    if not sorted_posts:
        raise AccessBlockedError(f"未能从 {url} 解析出帖子内容，该帖可能需要登录或触发了访问限制。")

    op_author = sorted_posts[0].author
    for idx, p in enumerate(sorted_posts, start=1):
        p.floor = idx
        if p.author == op_author:
            p.is_lz = True

    # Detect FangSong preference if OP explicitly used 仿宋
    prefer_fs = False
    op_first = BeautifulSoup(html1, "lxml").select_one("div#postlist > div[id^='post_'] td.t_f")
    if op_first and "仿宋" in str(op_first):
        prefer_fs = True

    note = f"注：原帖共 {total_pages} 页，本次抓取了前 {fetch_limit} 页（可通过 --max-pages 0 抓取全部）。" if fetch_limit < total_pages else None
    return ThreadData(
        platform="Discuz! 论坛",
        domain=parsed.netloc,
        source_url=f"{base_url}forum.php?mod=viewthread&tid={tid}",
        thread_id=f"tid-{tid}",
        title=thread_title or f"Thread {tid}",
        forum_name=forum_name or "论坛",
        total_pages=fetch_limit,
        posts=sorted_posts,
        prefer_fangsong=prefer_fs,
        truncated_note=note,
    )


def _extract_v2ex(url: str, max_pages: int = 10) -> ThreadData:
    m = re.search(r"/t/(\d+)", url)
    if not m:
        raise UnsupportedPlatformError(f"无法从 V2EX 链接提取主题 ID：{url}")
    tid = m.group(1)

    topics = fetch_json(f"https://www.v2ex.com/api/topics/show.json?id={tid}")
    if not topics:
        raise AccessBlockedError(f"V2EX API 未返回主题 {tid} 的内容（可能为隐藏节点或需要登录）。")
    t = topics[0]
    replies_data = fetch_json(f"https://www.v2ex.com/api/replies/show.json?topic_id={tid}")

    op_author = t.get("member", {}).get("username", "匿名")
    op_html, op_len = clean_rich_html(t.get("content_rendered") or t.get("content", ""), "https://www.v2ex.com/")
    posts = [
        PostItem(
            floor=1,
            author=op_author,
            time=format_timestamp(t.get("created")),
            body_html=op_html,
            is_lz=True,
            text_len=op_len,
        )
    ]

    for idx, r in enumerate(replies_data, start=2):
        r_author = r.get("member", {}).get("username", "匿名")
        r_html, r_len = clean_rich_html(r.get("content_rendered") or r.get("content", ""), "https://www.v2ex.com/")
        posts.append(
            PostItem(
                floor=idx,
                author=r_author,
                time=format_timestamp(r.get("created")),
                body_html=r_html,
                is_lz=(r_author == op_author),
                text_len=r_len,
            )
        )

    node_title = t.get("node", {}).get("title") or "V2EX"
    return ThreadData(
        platform="V2EX",
        domain="www.v2ex.com",
        source_url=f"https://www.v2ex.com/t/{tid}",
        thread_id=f"v2ex-{tid}",
        title=t.get("title", f"V2EX {tid}"),
        forum_name=node_title,
        total_pages=1,
        posts=posts,
    )


def _extract_hupu(url: str, max_pages: int = 10) -> ThreadData:
    m = re.search(r"bbs\.hupu\.com/(\d+)(?:-\d+)?\.html", url)
    if not m:
        raise UnsupportedPlatformError(f"无法从虎扑链接提取帖子 ID：{url}")
    tid = m.group(1)
    base_url = "https://bbs.hupu.com/"

    def parse_hupu_page(page_num: int) -> dict:
        target = f"https://bbs.hupu.com/{tid}.html" if page_num == 1 else f"https://bbs.hupu.com/{tid}-{page_num}.html"
        html = fetch_text(target)
        soup = BeautifulSoup(html, "lxml")
        nd = soup.find("script", id="__NEXT_DATA__")
        if not nd or not nd.string:
            raise AccessBlockedError(f"虎扑页面未包含 __NEXT_DATA__：{target}")
        data = json.loads(nd.string)
        return data.get("props", {}).get("pageProps", {}).get("detail", {})

    detail1 = parse_hupu_page(1)
    t = detail1.get("thread", {})
    if not t:
        raise AccessBlockedError(f"未能获取虎扑帖子 {tid} 内容。")

    replies_meta = detail1.get("replies", {})
    total_pages = int(replies_meta.get("total") or 1)
    fetch_limit = total_pages if max_pages <= 0 else min(total_pages, max_pages)

    op_author = (t.get("author") or {}).get("puname") or "匿名"
    op_html, op_len = clean_rich_html(t.get("content", ""), base_url)
    posts = [
        PostItem(
            floor=1,
            author=op_author,
            time=t.get("createdAtFormat") or format_timestamp(t.get("createdAt")),
            body_html=op_html,
            is_lz=True,
            text_len=op_len,
        )
    ]

    # Map lights count by pid
    light_counts: dict[str, int] = {}
    for lt in detail1.get("lights", []):
        light_counts[str(lt.get("pid"))] = int(lt.get("allLightCount") or lt.get("count") or 0)

    seen_pids = set()
    floor_counter = 2
    for pg in range(1, fetch_limit + 1):
        d = detail1 if pg == 1 else parse_hupu_page(pg)
        for r in d.get("replies", {}).get("list", []):
            pid = str(r.get("pid"))
            if pid in seen_pids or r.get("isDelete"):
                continue
            seen_pids.add(pid)
            r_author = (r.get("author") or {}).get("puname") or "匿名"
            r_html, r_len = clean_rich_html(r.get("content", ""), base_url)
            q_obj = r.get("quote")
            q_text = None
            if isinstance(q_obj, dict) and q_obj.get("content"):
                q_author = q_obj.get("authorName") or (q_obj.get("author") or {}).get("puname") or ""
                q_body = BeautifulSoup(q_obj["content"], "lxml").get_text(" ", strip=True)
                q_text = f"{q_author}：{q_body}" if q_author else q_body
            l_cnt = max(int(r.get("allLightCount") or 0), light_counts.get(pid, 0))
            posts.append(
                PostItem(
                    floor=floor_counter,
                    author=r_author,
                    time=r.get("createdAtFormat") or format_timestamp(r.get("createdAt")),
                    body_html=r_html,
                    is_lz=bool(r.get("isStarter") or r_author == op_author),
                    light_count=l_cnt,
                    quote_text=q_text,
                    text_len=r_len,
                )
            )
            floor_counter += 1

    topic_name = (t.get("topic") or {}).get("name") or "步行街"
    note = f"注：原帖共 {total_pages} 页，本次抓取了前 {fetch_limit} 页（可通过 --max-pages 0 抓取全部）。" if fetch_limit < total_pages else None
    return ThreadData(
        platform="虎扑社区",
        domain="bbs.hupu.com",
        source_url=f"https://bbs.hupu.com/{tid}.html",
        thread_id=f"hupu-{tid}",
        title=t.get("title") or f"虎扑帖子 {tid}",
        forum_name=topic_name,
        total_pages=fetch_limit,
        posts=posts,
        truncated_note=note,
    )


def _extract_sspai(url: str, max_pages: int = 10) -> ThreadData:
    m = re.search(r"sspai\.com/post/(\d+)", url)
    if not m:
        raise UnsupportedPlatformError(f"无法从少数派链接提取文章 ID：{url}")
    aid = m.group(1)

    res = fetch_json(f"https://sspai.com/api/v1/article/info/get?id={aid}&view=second&support_webp=true")
    data = res.get("data") or {}
    if not data or not data.get("body"):
        raise AccessBlockedError(f"未能获取少数派文章 {aid} 正文（可能为付费栏目或不存在）。")

    author = (data.get("author") or {}).get("nickname") or "匿名"
    pub_time = format_timestamp(data.get("released_time"))
    body_html, body_len = clean_rich_html(data.get("body", ""), "https://sspai.com/")
    posts = [
        PostItem(
            floor=1,
            author=author,
            time=pub_time,
            body_html=body_html,
            is_lz=True,
            text_len=body_len,
        )
    ]

    try:
        c_res = fetch_json(f"https://sspai.com/api/v1/comment/user/article/hot/page/get?article_id={aid}&limit=20&offset=0")
        for idx, c in enumerate(c_res.get("data") or [], start=2):
            c_author = (c.get("author") or {}).get("nickname") or "匿名"
            c_html, c_len = clean_rich_html(f"<p>{c.get('comment', '')}</p>", "https://sspai.com/")
            posts.append(
                PostItem(
                    floor=idx,
                    author=c_author,
                    time=format_timestamp(c.get("created_at")),
                    body_html=c_html,
                    is_lz=(c_author == author),
                    light_count=int(c.get("likes_count") or 0),
                    text_len=c_len,
                )
            )
    except Exception:
        pass

    tags = data.get("tags") or []
    tag_name = tags[0].get("title") if tags and isinstance(tags[0], dict) else "专栏文章"
    return ThreadData(
        platform="少数派",
        domain="sspai.com",
        source_url=f"https://sspai.com/post/{aid}",
        thread_id=f"sspai-{aid}",
        title=data.get("title") or f"少数派文章 {aid}",
        forum_name=tag_name,
        total_pages=1,
        posts=posts,
    )


def _extract_wechat(url: str, max_pages: int = 10) -> ThreadData:
    html = fetch_text(url)
    soup = BeautifulSoup(html, "lxml")

    title_el = soup.select_one("#activity-name")
    content_el = soup.select_one("#js_content")
    if not title_el or not content_el:
        raise AccessBlockedError("未能解析微信公众号正文，可能触发了微信验证或链接已失效。")

    title = title_el.get_text(strip=True)
    author_el = soup.select_one("#js_name")
    author = author_el.get_text(strip=True) if author_el else "微信公众号"

    ct_m = re.search(r'var\s+ct\s*=\s*"?(\d+)"?', html)
    pub_time = format_timestamp(ct_m.group(1)) if ct_m else ""

    body_html, body_len = clean_rich_html(str(content_el), "https://mp.weixin.qq.com/")
    short_id = re.sub(r"[^A-Za-z0-9_-]", "", url.split("/s/")[-1].split("?")[0])[:16] or "article"

    return ThreadData(
        platform="微信公众号",
        domain="mp.weixin.qq.com",
        source_url=url,
        thread_id=f"wx-{short_id}",
        title=title,
        forum_name=author,
        total_pages=1,
        posts=[
            PostItem(
                floor=1,
                author=author,
                time=pub_time,
                body_html=body_html,
                is_lz=True,
                text_len=body_len,
            )
        ],
    )


def _extract_cnblogs(url: str, max_pages: int = 10) -> ThreadData:
    html = fetch_text(url)
    soup = BeautifulSoup(html, "lxml")

    body_el = soup.find(id="cnblogs_post_body")
    if not body_el:
        raise AccessBlockedError(f"未能从博客园页面提取正文：{url}")

    title_el = soup.select_one("#cb_post_title_url")
    title = title_el.get_text(strip=True) if title_el else (soup.title.get_text(strip=True) if soup.title else "博客园文章")

    author_el = soup.select_one("#Header1_HeaderTitle, .author a")
    m_blog = re.search(r"cnblogs\.com/([^/]+)/", url)
    blog_app = m_blog.group(1) if m_blog else "cnblogs"
    author = author_el.get_text(strip=True) if author_el else blog_app

    date_el = soup.select_one("#post-date")
    pub_time = date_el.get_text(strip=True) if date_el else ""

    # Preserve <pre> code blocks cleanly
    body_html, body_len = clean_rich_html(str(body_el), url)
    m_pid = re.search(r"/p/([^/.?#]+)", url)
    pid = m_pid.group(1) if m_pid else "post"

    return ThreadData(
        platform="博客园",
        domain="www.cnblogs.com",
        source_url=url,
        thread_id=f"cnblogs-{pid}",
        title=title,
        forum_name=f"{blog_app} 的博客",
        total_pages=1,
        posts=[
            PostItem(
                floor=1,
                author=author,
                time=pub_time,
                body_html=body_html,
                is_lz=True,
                text_len=body_len,
            )
        ],
    )


def _extract_juejin(url: str, max_pages: int = 10) -> ThreadData:
    m = re.search(r"juejin\.cn/post/(\d+)", url)
    if not m:
        raise UnsupportedPlatformError(f"无法从稀土掘金链接提取文章 ID：{url}")
    aid = m.group(1)

    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.goto(f"https://juejin.cn/post/{aid}", wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_selector(".markdown-body", timeout=15000)
        except Exception as e:
            browser.close()
            raise AccessBlockedError(f"掘金页面未能加载正文（可能受登录或人机校验限制）：{url}") from e
        page.wait_for_timeout(1500)
        payload = page.evaluate("""() => ({
            title: document.querySelector('h1.article-title')?.innerText?.trim() || document.title,
            author: document.querySelector('.author-info-block .username .name, .author-name .name')?.innerText?.trim() || '匿名',
            time: document.querySelector('.meta-box time')?.innerText?.trim() || '',
            bodyHtml: document.querySelector('.markdown-body')?.innerHTML || '',
            comments: Array.from(document.querySelectorAll('.comment-list .comment')).slice(0, 15).map(el => ({
                author: el.querySelector('.user-box .name')?.innerText?.trim() || '匿名',
                time: el.querySelector('.time')?.innerText?.trim() || '',
                text: el.querySelector('.content')?.innerText?.trim() || ''
            })).filter(c => c.text)
        })""")
        browser.close()

    body_html, body_len = clean_rich_html(payload["bodyHtml"], "https://juejin.cn/")
    posts = [
        PostItem(
            floor=1,
            author=payload["author"],
            time=payload["time"],
            body_html=body_html,
            is_lz=True,
            text_len=body_len,
        )
    ]
    for idx, c in enumerate(payload.get("comments", []), start=2):
        c_html, c_len = clean_rich_html(f"<p>{c['text']}</p>", "https://juejin.cn/")
        posts.append(
            PostItem(
                floor=idx,
                author=c["author"],
                time=c["time"],
                body_html=c_html,
                is_lz=(c["author"] == payload["author"]),
                text_len=c_len,
            )
        )

    return ThreadData(
        platform="稀土掘金",
        domain="juejin.cn",
        source_url=f"https://juejin.cn/post/{aid}",
        thread_id=f"juejin-{aid}",
        title=payload["title"],
        forum_name="技术专栏",
        total_pages=1,
        posts=posts,
    )


def _extract_xiaoheihe(url: str, max_pages: int = 10) -> ThreadData:
    m = re.search(r"xiaoheihe\.cn/(?:app/bbs/link|bbs/post)/(\d+)", url)
    if not m:
        raise UnsupportedPlatformError(f"无法从小黑盒链接提取帖子 ID：{url}")
    link_id = m.group(1)

    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        tree_status = None

        def on_resp(resp):
            nonlocal tree_status
            if "/bbs/app/link/tree" in resp.url:
                try:
                    tree_status = resp.json().get("status")
                except Exception:
                    pass

        page.on("response", on_resp)
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(4000)

        if tree_status == "show_captcha":
            browser.close()
            raise AccessBlockedError(
                f"小黑盒帖子详情接口返回人机验证拦截（status: show_captcha）：{url}。"
                "受平台反爬/验证机制限制无法自动抓取，请在浏览器中自行完成验证后查看或手动保存页面。"
            )

        payload = page.evaluate("""() => {
            const root = document.querySelector('.hb-bbs-link');
            const title = document.querySelector('h1, .hb-bbs-link h1')?.innerText?.trim() || document.title;
            const body = root ? root.innerHTML : '';
            const textLen = root ? root.innerText.trim().length : 0;
            return { title, body, textLen };
        }""")
        browser.close()

    if payload["textLen"] < 30:
        raise AccessBlockedError(
            f"未能从小黑盒页面提取有效正文（可能触发登录或人机验证，status={tree_status}）：{url}"
        )

    body_html, body_len = clean_rich_html(payload["body"], "https://www.xiaoheihe.cn/")
    return ThreadData(
        platform="小黑盒",
        domain="www.xiaoheihe.cn",
        source_url=url,
        thread_id=f"heybox-{link_id}",
        title=payload["title"],
        forum_name="社区帖子",
        total_pages=1,
        posts=[
            PostItem(
                floor=1,
                author="小黑盒用户",
                time="",
                body_html=body_html,
                is_lz=True,
                text_len=body_len,
            )
        ],
    )


def extract_thread(url: str, max_pages: int = 10) -> ThreadData:
    host = urllib.parse.urlparse(url).netloc.lower()
    if "v2ex.com" in host:
        return _extract_v2ex(url, max_pages=max_pages)
    if "bbs.hupu.com" in host:
        return _extract_hupu(url, max_pages=max_pages)
    if "sspai.com" in host:
        return _extract_sspai(url, max_pages=max_pages)
    if "mp.weixin.qq.com" in host:
        return _extract_wechat(url, max_pages=max_pages)
    if "cnblogs.com" in host:
        return _extract_cnblogs(url, max_pages=max_pages)
    if "juejin.cn" in host:
        return _extract_juejin(url, max_pages=max_pages)
    if "xiaoheihe.cn" in host:
        return _extract_xiaoheihe(url, max_pages=max_pages)
    if (
        any(d in host for d in ("chongbuluo.com", "52pojie.cn", "chiphell.com", "hostloc.com"))
        or "mod=viewthread" in url
        or re.search(r"thread-\d+-\d+-\d+\.html", url)
    ):
        return _extract_discuz(url, max_pages=max_pages)

    raise UnsupportedPlatformError(
        f"暂不支持该平台域名：{host}。当前支持 Discuz!(虫部落/52pojie等)、V2EX、虎扑、少数派、微信公众号、博客园、稀土掘金。"
    )
