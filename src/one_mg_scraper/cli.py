from __future__ import annotations

import argparse
import asyncio
import os

from .index_crawler import crawl_index
from .detail_scraper import scrape_details
from .utils import read_jsonl_urls, write_jsonl


def cmd_crawl_index(args) -> None:
    out_path = args.out
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    seen = read_jsonl_urls(out_path, url_key="url")
    rows, pages = crawl_index(
        out_seen=seen,
        labels=args.labels,
        sleep_s=args.sleep,
        max_pages_per_label=args.max_pages,
    )
    if rows:
        write_jsonl(out_path, rows)

    print(f"Index crawl done. Pages fetched: {pages}. New URLs written: {len(rows)}. Total seen now: {len(seen)}")
    print(f"Output: {out_path}")


def cmd_scrape_details(args) -> None:
    scraped_ok, blocked = asyncio.run(
        scrape_details(
            urls_jsonl=args.urls,
            out_jsonl=args.out_jsonl,
            out_csv=args.out_csv,
            concurrency=args.concurrency,
            delay_s=args.delay,
            limit=args.limit,
            headed=args.headed,
            profile_dir=args.profile,
            manual_challenge=args.manual_challenge,
        )
    )
    print("Detail scrape done.")
    print(f"OK scraped: {scraped_ok}")
    print(f"Blocked/captcha: {blocked}")
    print(f"JSONL: {args.out_jsonl}")
    print(f"CSV:   {args.out_csv}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="one_mg_scraper")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("crawl-index", help="Crawl /drugs-all-medicines A–Z index and collect drug URLs")
    a.add_argument("--labels", default="A-Z", help='A-Z or a subset like "ABC"')
    a.add_argument("--sleep", type=float, default=1.2, help="Delay between index requests (seconds)")
    a.add_argument("--max-pages", type=int, default=None, help="Safety cap per label")
    a.add_argument("--out", default="data/urls/urls.jsonl", help="Output JSONL for URLs")
    a.set_defaults(func=cmd_crawl_index)

    b = sub.add_parser("scrape-details", help="Scrape detailed fields from each drug page URL")
    b.add_argument("--urls", default="data/urls/urls.jsonl", help="Input URL JSONL from crawl-index")
    b.add_argument("--out-jsonl", default="data/output/medicines.jsonl", help="Output JSONL for detailed data")
    b.add_argument("--out-csv", default="data/output/medicines.csv", help="Output CSV (flattened)")
    b.add_argument("--concurrency", type=int, default=1, help="Parallel pages (keep low)")
    b.add_argument("--delay", type=float, default=3.0, help="Delay per URL (seconds)")
    b.add_argument("--limit", type=int, default=None, help="Limit number of URLs scraped (debug)")
    b.add_argument("--headed", action="store_true", help="Run with visible browser window")
    b.add_argument("--profile", default="data/profile", help="Persistent profile dir for cookies/storage")
    b.add_argument("--manual-challenge", action="store_true", help="If blocked, allow manual solve then continue")
    b.set_defaults(func=cmd_scrape_details)

    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()