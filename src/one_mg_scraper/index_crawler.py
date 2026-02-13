from __future__ import annotations

import re
import string
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


BASE = "https://www.1mg.com"
INDEX_PATH = "/drugs-all-medicines"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0; +https://example.com/bot)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Connection": "keep-alive",
}

# On index pages, drug cards are plain <a href="/drugs/...">...</a>
DRUG_HREF_RE = re.compile(r"^/drugs/")

@dataclass
class IndexItem:
    url: str
    label: str
    page: int
    raw_text: str


@retry(
    reraise=True,
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
)
def fetch_html(client: httpx.Client, url: str) -> str:
    r = client.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def parse_index_page(html: str, label: str, page: int) -> List[IndexItem]:
    soup = BeautifulSoup(html, "lxml")
    anchors = soup.select('a[href^="/drugs/"]')
    out: List[IndexItem] = []
    for a in anchors:
        href = a.get("href", "").strip()
        if not href or not DRUG_HREF_RE.match(href):
            continue
        raw_text = " ".join(a.stripped_strings)
        raw_text = re.sub(r"\s+", " ", raw_text).strip()
        out.append(IndexItem(url=BASE + href, label=label, page=page, raw_text=raw_text))
    return out


def label_list(labels: str) -> List[str]:
    if labels.strip().upper() == "A-Z":
        return list(string.ascii_uppercase)
    return [c.upper() for c in labels.strip() if c.isalpha()]


def crawl_index(
    out_seen: Set[str],
    labels: str = "A-Z",
    sleep_s: float = 1.2,
    max_pages_per_label: Optional[int] = None,
) -> Tuple[List[Dict], int]:
    """
    Returns:
      - new rows to write (as dicts)
      - count of pages fetched
    """
    rows: List[Dict] = []
    pages_fetched = 0

    with httpx.Client(headers=DEFAULT_HEADERS, follow_redirects=True) as client:
        for lab in label_list(labels):
            page = 1
            while True:
                if max_pages_per_label and page > max_pages_per_label:
                    break

                url = f"{BASE}{INDEX_PATH}?label={lab}&page={page}"
                html = fetch_html(client, url)
                pages_fetched += 1

                items = parse_index_page(html, lab, page)

                # stop condition: no drug links
                if not items:
                    break

                new_count = 0
                for it in items:
                    if it.url in out_seen:
                        continue
                    out_seen.add(it.url)
                    rows.append(
                        {
                            "url": it.url,
                            "label": it.label,
                            "page": it.page,
                            "raw_text": it.raw_text,
                        }
                    )
                    new_count += 1

                page += 1
                time.sleep(sleep_s)

            time.sleep(sleep_s)

    return rows, pages_fetched