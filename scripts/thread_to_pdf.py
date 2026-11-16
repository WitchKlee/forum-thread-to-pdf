#!/usr/bin/env python3
"""
CLI entrypoint for forum-thread-to-pdf.
Exports threads/articles from mainstream Chinese platforms into clean A4 PDFs.
"""

from __future__ import annotations

import argparse
import os
import sys

# Ensure sibling modules import cleanly regardless of current working directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from adapters import extract_thread
from core import AccessBlockedError, UnsupportedPlatformError, render_thread_pdf, sanitize_filename


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export a forum thread or article from mainstream Chinese platforms to A4 PDF."
    )
    parser.add_argument("url", help="Thread or article URL")
    parser.add_argument("-o", "--output", help="Output PDF path (defaults to <title>.pdf in CWD)")
    parser.add_argument("--mode", choices=["auto", "article", "qa"], default="auto", help="Layout mode")
    parser.add_argument("--replies", choices=["all", "lz-only", "none"], default="all", help="Replies to include")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=10,
        help="Max pages to fetch for multi-page threads (0 = fetch all pages, default: 10)",
    )
    parser.add_argument("--html-out", help="Optional path to keep intermediate HTML")
    parser.add_argument("--verify-dir", help="Optional directory to output first/last page PNG previews")
    args = parser.parse_args()

    try:
        thread = extract_thread(args.url, max_pages=args.max_pages)
        pdf_path = args.output or os.path.join(os.getcwd(), f"{sanitize_filename(thread.title)}.pdf")
        info = render_thread_pdf(
            thread=thread,
            pdf_path=pdf_path,
            mode=args.mode,
            replies_scope=args.replies,
            html_out=args.html_out,
            verify_dir=args.verify_dir,
        )
    except (AccessBlockedError, UnsupportedPlatformError) as e:
        print(f"[BLOCKED/UNSUPPORTED] {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(
        f"Platform: {info['platform']} | Mode: {info['mode']} | "
        f"Pages fetched: {info['total_pages_fetched']} | Posts: {info['posts_included']}"
    )
    print(f"Title: {info['title']}")
    print(f"Saved PDF: {info['pdf_path']} ({info['pdf_bytes']} bytes)")
    if "pdf_pages" in info:
        print(f"PDF pages: {info['pdf_pages']} | Chars/page: {info['page_chars']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
