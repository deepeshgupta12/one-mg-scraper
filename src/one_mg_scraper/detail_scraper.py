from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from .parser import parse_1mg_rendered_html
from .utils import ensure_dir, read_jsonl_urls, write_jsonl


def read_urls_jsonl(path: str) -> List[Dict[str, Any]]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def flatten_for_csv(item: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(item)
    for k in ["uses", "quick_tips_or_facts", "substitutes", "patient_concerns", "fact_box", "marketer_details"]:
        if k in out:
            out[k] = json.dumps(out[k], ensure_ascii=False)
    return out


def weak_block_signals(html: str) -> bool:
    """
    Conservative check: only treat as blocked if BOTH:
      - we can't find core page structure (handled elsewhere), AND
      - we see strong block indicators
    """
    low = html.lower()
    signals = [
        "cf-turnstile",
        "/cdn-cgi/",
        "access denied",
        "pardon the interruption",
        "unusual traffic",
        "verify you are human",
    ]
    return any(s in low for s in signals)


async def page_has_drug_content(page) -> bool:
    """
    True if the drug page likely loaded.
    The single most reliable indicator is the H1 (drug name).
    """
    try:
        await page.wait_for_selector("h1", timeout=15000)
        # extra sanity: h1 should have non-trivial text
        h1 = await page.query_selector("h1")
        if not h1:
            return False
        txt = (await h1.inner_text()).strip()
        return len(txt) >= 3
    except PlaywrightTimeoutError:
        return False


async def scrape_one(
    page,
    url: str,
    headed: bool = False,
    manual_challenge: bool = False,
    wait_ms: int = 1200,
) -> Dict[str, Any]:
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        status = resp.status if resp else None

        # Give hydration some time
        await page.wait_for_timeout(wait_ms)

        # Primary success condition: drug content present
        if await page_has_drug_content(page):
            html = await page.content()
            data = parse_1mg_rendered_html(html, url)
            data["http_status"] = status
            if data.get("name"):
                return data
            return {"url": url, "http_status": status, "error": "parsed_empty_name_but_h1_present"}

        # Not loaded yet; grab HTML and evaluate strong block signals
        html = await page.content()

        # If the server is explicitly refusing, mark it
        if status in (403, 429, 503):
            # allow manual step if headed and enabled
            if headed and manual_challenge:
                print("\n[MANUAL STEP REQUIRED]")
                print(f"Server returned {status}. If the page is visible in the browser, wait till it loads.")
                print(f"URL: {url}")
                print("Press Enter after it loads fully...\n")
                await asyncio.to_thread(input, "")
                await page.wait_for_timeout(1500)

                if await page_has_drug_content(page):
                    html2 = await page.content()
                    data2 = parse_1mg_rendered_html(html2, url)
                    data2["http_status"] = status
                    if data2.get("name"):
                        return data2
                    return {"url": url, "http_status": status, "error": "parsed_empty_name_after_manual"}

            return {"url": url, "http_status": status, "error": "http_blocked"}

        # If we see strong block signals AND no H1 -> likely blocked
        if weak_block_signals(html):
            if headed and manual_challenge:
                print("\n[MANUAL STEP REQUIRED]")
                print(f"Open browser window and ensure the drug page content loads for:\n  {url}")
                print("If anything is shown that needs interaction, do it as a human.")
                print("Then press Enter here to continue...\n")
                await asyncio.to_thread(input, "")
                await page.wait_for_timeout(1500)

                if await page_has_drug_content(page):
                    html2 = await page.content()
                    data2 = parse_1mg_rendered_html(html2, url)
                    data2["http_status"] = status
                    if data2.get("name"):
                        return data2
                    return {"url": url, "http_status": status, "error": "parsed_empty_name_after_manual"}

                return {"url": url, "http_status": status, "error": "blocked_or_captcha_after_manual"}

            return {"url": url, "http_status": status, "error": "blocked_or_captcha"}

        # Otherwise this is likely just slow loading / JS render differences
        # Try a longer wait once
        await page.wait_for_timeout(4000)
        if await page_has_drug_content(page):
            html3 = await page.content()
            data3 = parse_1mg_rendered_html(html3, url)
            data3["http_status"] = status
            if data3.get("name"):
                return data3
            return {"url": url, "http_status": status, "error": "parsed_empty_name_after_retry"}

        return {"url": url, "http_status": status, "error": "no_h1_or_sections_loaded"}

    except Exception as e:
        return {"url": url, "error": str(e)}


async def scrape_details(
    urls_jsonl: str,
    out_jsonl: str,
    out_csv: str,
    concurrency: int = 1,
    delay_s: float = 3.0,
    limit: Optional[int] = None,
    headed: bool = False,
    profile_dir: str = "data/profile",
    manual_challenge: bool = False,
) -> Tuple[int, int]:
    ensure_dir(os.path.dirname(out_jsonl))
    ensure_dir(os.path.dirname(out_csv))
    ensure_dir(profile_dir)

    all_urls_rows = read_urls_jsonl(urls_jsonl)
    urls = [r["url"] for r in all_urls_rows if "url" in r]

    done: Set[str] = read_jsonl_urls(out_jsonl, url_key="url")
    queue = [u for u in urls if u not in done]
    if limit:
        queue = queue[:limit]

    sem = asyncio.Semaphore(concurrency)
    results_buffer: List[Dict[str, Any]] = []

    scraped_ok = 0
    blocked_count = 0

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=not headed,
            viewport={"width": 1365, "height": 768},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        )

        async def worker(u: str):
            async with sem:
                page = await context.new_page()
                item = await scrape_one(page, u, headed=headed, manual_challenge=manual_challenge)
                await page.close()
                await asyncio.sleep(delay_s)
                return item

        tasks = [asyncio.create_task(worker(u)) for u in queue]

        for coro in asyncio.as_completed(tasks):
            item = await coro
            if item:
                err = item.get("error", "")
                if err in ("blocked_or_captcha", "blocked_or_captcha_after_manual", "http_blocked"):
                    blocked_count += 1
                elif "error" not in item:
                    scraped_ok += 1

                results_buffer.append(item)

            if len(results_buffer) >= 25:
                write_jsonl(out_jsonl, results_buffer)
                results_buffer = []

        if results_buffer:
            write_jsonl(out_jsonl, results_buffer)

        await context.close()

    # build CSV
    rows = []
    with open(out_jsonl, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))

    df = pd.DataFrame([flatten_for_csv(r) for r in rows])
    df.to_csv(out_csv, index=False, encoding="utf-8")

    return scraped_ok, blocked_count