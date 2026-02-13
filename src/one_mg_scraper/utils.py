from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, Set


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def write_jsonl(path: str, rows: Iterable[Dict[str, Any]]) -> None:
    ensure_dir(os.path.dirname(path))
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl_urls(path: str, url_key: str = "url") -> Set[str]:
    if not os.path.exists(path):
        return set()
    seen = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                u = obj.get(url_key)
                if u:
                    seen.add(u)
            except Exception:
                continue
    return seen


def now_ts() -> float:
    return time.time()