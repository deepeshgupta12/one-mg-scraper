from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup, Tag


# -----------------------
# Text helpers
# -----------------------
_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")

# Strong noise signals we should never treat as product content
_NAV_NOISE_PHRASES = [
    "Medicines",
    "Lab Tests",
    "Consult Doctors",
    "Login",
    "Sign Up",
    "Offers",
    "Need Help?",
    "Search for Medicines",
    "Health Resource Center",
    "All Diseases",
    "All Medicines",
    "Medicines by Therapeutic Class",
    "Care Plan",
]

# Section boundaries that sometimes get embedded inside a big container
_BOUNDARY_PHRASES = [
    "Uses of",
    "Uses",
    "Benefits of",
    "Benefits",
    "Key benefits",
    "Side effects of",
    "Side effects",
    "Quick tips",
    "Quick tip",
    "Fact box",
    "FAQs",
    "Patient concerns",
    "Marketer details",
    "All substitutes",
    "Substitutes",
    "How to use",
    "How to take",
]

# For trimming noisy suffixes inside sections
_STOP_AT_PHRASES = [
    "How to use",
    "How to take",
    "Storage",
    "Interactions",
    "Drug interactions",
    "Patient concerns",
    "Marketer details",
    "All substitutes",
    "Substitutes",
]


def _clean_ws(s: str) -> str:
    return _WS_RE.sub(" ", (s or "")).strip()


def _strip_html(s: str) -> str:
    # JSON-LD FAQ answers sometimes contain basic HTML tags.
    return _clean_ws(_TAG_RE.sub(" ", s or ""))


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for it in items:
        it = _clean_ws(it)
        if not it:
            continue
        k = it.lower()
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def _heading_level(tag: Tag) -> int:
    if tag and tag.name and tag.name.startswith("h") and tag.name[1:].isdigit():
        return int(tag.name[1:])
    return 10


def _is_heading(tag: Tag) -> bool:
    return isinstance(tag, Tag) and tag.name in ("h1", "h2", "h3", "h4", "h5", "h6")


def _text(tag: Tag) -> str:
    return _clean_ws(" ".join(tag.stripped_strings)) if isinstance(tag, Tag) else ""


def _looks_like_nav_noise(txt: str) -> bool:
    if not txt:
        return True
    low = txt.lower()

    # Strong indicators: pipes / menu-like
    if " | " in txt or "login" in low and "sign up" in low:
        return True

    # Too many nav tokens often appear together
    hits = 0
    for p in _NAV_NOISE_PHRASES:
        if p.lower() in low:
            hits += 1
            if hits >= 3:
                return True

    # No sentence structure & too many short words can be header/menu dumps
    # (we still allow legitimate short sections elsewhere)
    return False


def _truncate_at_phrases(txt: str, phrases: List[str]) -> str:
    txt = _clean_ws(txt)
    if not txt:
        return ""
    low = txt.lower()
    cut = len(txt)
    for p in phrases:
        idx = low.find(p.lower())
        if idx != -1 and idx > 40:
            cut = min(cut, idx)
    return _clean_ws(txt[:cut])


def _dedupe_sentences(txt: str) -> str:
    txt = _clean_ws(txt)
    if not txt:
        return ""
    # Split lightly on sentence ends; keep it conservative.
    parts = re.split(r"(?<=[.!?])\s+", txt)
    out: List[str] = []
    seen: set[str] = set()
    for p in parts:
        p = _clean_ws(p)
        if not p:
            continue
        k = p.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return _clean_ws(" ".join(out))


def _compress_repeat_runs(txt: str) -> str:
    """
    Removes obvious repeated blocks inside a long string, e.g.:
    "How to use ... How to use ... How to use ..."
    Works on token windows and is safe for natural text.
    """
    txt = _clean_ws(txt)
    toks = txt.split()
    if len(toks) < 60:
        return txt

    # try removing repeated windows from larger to smaller
    for n in range(25, 8, -1):
        i = 0
        out: List[str] = []
        while i < len(toks):
            if i + 2 * n <= len(toks) and toks[i : i + n] == toks[i + n : i + 2 * n]:
                # keep one occurrence, skip the repeats
                out.extend(toks[i : i + n])
                j = i + n
                while j + n <= len(toks) and toks[i : i + n] == toks[j : j + n]:
                    j += n
                i = j
            else:
                out.append(toks[i])
                i += 1
        toks = out

    return _clean_ws(" ".join(toks))


# -----------------------
# DOM section helpers
# -----------------------
def _section_nodes(heading: Tag) -> List[Tag]:
    """
    Collect nodes after `heading` until the next heading of the SAME or HIGHER level.
    This is the key fix: do not stop at subheadings (e.g., h3 under an h2 section).
    """
    cur = _heading_level(heading)
    nodes: List[Tag] = []

    for el in heading.find_all_next(True):
        if el is heading:
            continue
        if _is_heading(el) and _heading_level(el) <= cur:
            break
        nodes.append(el)
    return nodes


def _section_text(heading: Tag) -> str:
    """
    Best-effort readable text for a section (keeps headings inside the section).
    Additionally guards against nav/header dumps by stopping at known boundaries.
    """
    nodes = _section_nodes(heading)
    parts: List[str] = []
    for el in nodes:
        if not isinstance(el, Tag):
            continue
        if el.name in ("script", "style", "noscript"):
            continue

        t = _text(el)
        if not t:
            continue

        # Safety: if a node itself is clearly a boundary label, stop
        if any(t.lower().startswith(bp.lower()) for bp in _BOUNDARY_PHRASES) and len(t) <= 60:
            break

        # Safety: avoid grabbing mega nav blocks (esp. for "product_information")
        if _looks_like_nav_noise(t) and len(parts) == 0 and len(t) > 120:
            continue

        # Allow common content tags
        if el.name in ("p", "li", "h3", "h4", "h5", "h6", "div", "span"):
            parts.append(t)

    txt = _clean_ws(" ".join(parts))
    txt = _truncate_at_phrases(txt, _STOP_AT_PHRASES)
    txt = _compress_repeat_runs(txt)
    txt = _dedupe_sentences(txt)
    return txt


def _format_strength(available_strength: Any) -> str:
    """
    1mg Drug JSON-LD often provides:
    - availableStrength: [{"strengthValue": "100", "strengthUnit": "mg"}]
    """
    if available_strength is None:
        return ""
    if isinstance(available_strength, str):
        return _clean_ws(available_strength)
    if isinstance(available_strength, dict):
        v = available_strength.get("strengthValue") or available_strength.get("value") or ""
        u = available_strength.get("strengthUnit") or available_strength.get("unit") or ""
        return f"{v}{u}".replace(" ", "").strip()
    if isinstance(available_strength, list) and available_strength:
        return _format_strength(available_strength[0])
    return _clean_ws(str(available_strength))


# -----------------------
# JSON-LD extraction
# -----------------------
def _parse_jsonld(soup: BeautifulSoup) -> Tuple[Optional[dict], Optional[dict]]:
    """
    Returns (drug_json, faq_json) when present.
    Handles:
    - top-level objects
    - lists
    - @graph containers
    """
    drug: Optional[dict] = None
    faq: Optional[dict] = None

    def scan_obj(obj: Any) -> None:
        nonlocal drug, faq
        if not isinstance(obj, dict):
            return

        # @graph support
        if isinstance(obj.get("@graph"), list):
            for g in obj["@graph"]:
                scan_obj(g)

        t = obj.get("@type")
        if isinstance(t, list):
            if "Drug" in t and drug is None:
                drug = obj
            if "FAQPage" in t and faq is None:
                faq = obj
        else:
            if t == "Drug" and drug is None:
                drug = obj
            if t == "FAQPage" and faq is None:
                faq = obj

    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (s.string or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue

        if isinstance(data, list):
            for item in data:
                scan_obj(item)
        else:
            scan_obj(data)

    return drug, faq


# -----------------------
# Header meta rows (Storage, Marketer, Salt, etc.)
# -----------------------
def _extract_header_meta(soup: BeautifulSoup) -> Dict[str, str]:
    """
    1mg pages include a key-value block near the top with rows like:
      Storage | Store in a refrigerator (2 - 8°C). Do not freeze.
      Marketer | Roche Products India Pvt Ltd
      Salt Composition | Bevacizumab (100mg)
    """
    meta_raw: Dict[str, str] = {}

    title_divs = soup.find_all("div", class_=re.compile(r"DrugHeader__meta-title", re.I))
    for tdiv in title_divs:
        key = _clean_ws(" ".join(tdiv.stripped_strings))
        if not key:
            continue
        parent = tdiv.parent
        if not isinstance(parent, Tag):
            continue
        info = parent.find(["div", "span"], class_=re.compile(r"DrugHeader__meta-(?:info|value)", re.I))
        if info:
            val = _clean_ws(" ".join(info.stripped_strings))
            if val and key not in meta_raw:
                meta_raw[key] = val

    # Normalize common key variants -> canonical
    canon_map = {
        "salt composition": "Salt Composition",
        "salt": "Salt Composition",
        "storage": "Storage",
        "marketer": "Marketer",
        "prescription": "Prescription",
        "prescription required": "Prescription Required",
    }

    meta: Dict[str, str] = {}
    for k, v in meta_raw.items():
        kl = _clean_ws(k).lower()
        ck = canon_map.get(kl, k)
        if ck not in meta:
            meta[ck] = v

    return meta


# -----------------------
# Section-specific extractors
# -----------------------
def _find_heading(soup: BeautifulSoup, startswith_lower: str) -> Optional[Tag]:
    startswith_lower = startswith_lower.lower()
    for tag in soup.find_all(["h2", "h3", "h4", "h5"]):
        t = _text(tag).lower()
        if t.startswith(startswith_lower):
            return tag
    return None


def _find_heading_any(soup: BeautifulSoup, prefixes: List[str]) -> Optional[Tag]:
    for p in prefixes:
        h = _find_heading(soup, p)
        if h:
            return h
    return None


def _extract_product_information(soup: BeautifulSoup, drug_json: Optional[dict]) -> str:
    """
    Fix:
    - Avoid capturing header/navigation dump (the "Medicines Lab Tests ..." mega string).
    Strategy:
      1) Prefer section headings: "Product introduction", "Product information", "Introduction", "Overview"
      2) Else choose a top-of-page paragraph/div that looks like a real description (has sentences, not nav)
      3) Else fallback to JSON-LD description (if present)
    """
    h = _find_heading_any(soup, ["product introduction", "product information", "introduction", "overview"])
    txt = _section_text(h) if h else ""

    if not txt:
        candidates: List[str] = []
        # Prefer paragraphs first
        for el in soup.find_all(["p", "div"]):
            if not isinstance(el, Tag):
                continue
            if el.find_parent(["script", "style", "header", "nav", "footer"]):
                continue

            t = _text(el)
            if not t:
                continue
            if _looks_like_nav_noise(t):
                continue
            # Must look like a description: has a sentence or "is used" style phrasing
            low = t.lower()
            if len(t) < 180:
                continue
            if not (("." in t) or (" is used " in low) or (" used to " in low) or (" helps " in low)):
                continue
            # Avoid content that already includes other section labels
            if any(bp.lower() in low for bp in ["uses of", "benefits of", "side effects", "quick tips", "fact box"]):
                t = _truncate_at_phrases(t, _BOUNDARY_PHRASES)
            if len(t) >= 180:
                candidates.append(t)

        txt = max(candidates, key=len) if candidates else ""

    # Final trims & de-dup
    txt = _truncate_at_phrases(txt, _BOUNDARY_PHRASES)
    txt = _compress_repeat_runs(txt)
    txt = _dedupe_sentences(txt)

    if not txt and drug_json:
        txt = _clean_ws(str(drug_json.get("description") or ""))

    return txt


def _extract_uses(soup: BeautifulSoup) -> List[str]:
    # Support "Uses of" and sometimes just "Uses"
    h = _find_heading_any(soup, ["uses of", "uses"])
    if not h:
        return []
    nodes = _section_nodes(h)

    items: List[str] = []

    # Prefer the first UL list
    for ul in [n for n in nodes if isinstance(n, Tag) and n.name == "ul"]:
        its = [_text(li) for li in ul.find_all("li")]
        its = [x for x in its if x]
        if its:
            items = its
            break

    if not items:
        for el in nodes:
            if isinstance(el, Tag) and el.name == "li":
                t = _text(el)
                if t:
                    items.append(t)

    return _dedupe_preserve_order(items)


def _extract_benefits(soup: BeautifulSoup) -> Tuple[str, List[Dict[str, str]]]:
    """
    Fixes:
    - Heading can be "Benefits", "Benefits of", or "Key benefits"
    - Prevent the last "In <use>" block from swallowing embedded "Side effects ..." content
    - More robust "In <use>" detection (can be h3/h4 or accordion labels)
    """
    h = _find_heading_any(soup, ["benefits of", "benefits", "key benefits"])
    if not h:
        return "", []

    nodes = _section_nodes(h)
    blocks: List[Dict[str, str]] = []

    cur_use: Optional[str] = None
    cur_parts: List[str] = []
    cur_seen: set[str] = set()

    def flush() -> None:
        nonlocal cur_use, cur_parts, cur_seen
        if cur_use:
            text = _clean_ws(" ".join(cur_parts))
            text = _truncate_at_phrases(text, ["Side effects", "Side effects of", "How to use", "How to take"])
            text = _compress_repeat_runs(text)
            text = _dedupe_sentences(text)
            if text:
                blocks.append({"use": cur_use, "text": text})
        cur_use = None
        cur_parts = []
        cur_seen = set()

    for el in nodes:
        if not isinstance(el, Tag):
            continue
        if el.name in ("script", "style", "noscript"):
            continue

        # Detect "In <use>" on headings or label-like blocks
        if _is_heading(el):
            ht = _text(el)
            if ht.lower().startswith("in ") and len(ht) <= 90:
                flush()
                cur_use = ht[3:].strip()
                continue
        else:
            # Sometimes it's a div/span label
            tlabel = _text(el)
            if tlabel.lower().startswith("in ") and len(tlabel) <= 90:
                flush()
                cur_use = tlabel[3:].strip()
                continue

        if not cur_use:
            continue

        t = _text(el)
        if not t:
            continue

        # Stop if we hit embedded boundaries
        low = t.lower()
        for stop in ["side effects", "side effects of", "how to use", "how to take", "patient concerns", "marketer details"]:
            idx = low.find(stop)
            if idx != -1 and idx > 0:
                t = t[:idx].strip()
                break
        t = _clean_ws(t)
        if not t:
            continue

        # Skip re-printed accordion headers like "In ..."
        if t.lower().startswith("in ") and len(t) <= 90:
            continue

        # Avoid one element accidentally containing another "In <use>" block
        m = re.search(r"\bIn\s+[A-Z]", t)
        if m:
            t = t[: m.start()].strip()

        if len(t) < 20:
            continue

        k = t.lower()
        if k in cur_seen:
            continue
        cur_seen.add(k)
        cur_parts.append(t)

    flush()

    if blocks:
        benefits_text = " ".join([f"In {b['use']} {b['text']}" for b in blocks]).strip()
    else:
        benefits_text = _section_text(h)

    benefits_text = _truncate_at_phrases(benefits_text, ["Side effects", "How to use", "How to take"])
    benefits_text = _compress_repeat_runs(benefits_text)
    benefits_text = _dedupe_sentences(benefits_text)

    return benefits_text, blocks


def _extract_side_effects(soup: BeautifulSoup) -> Tuple[str, List[str]]:
    """
    Fixes:
    - Robustly find "Common side effects" even if not a heading tag
    - Stop section text before "How to use" to prevent duplication spills
    - Dedupe repeated paragraphs
    """
    h = _find_heading_any(soup, ["side effects of", "side effects"])
    if not h:
        return "", []

    nodes = _section_nodes(h)

    # Readable section text (stop at "How to use" / similar)
    parts: List[str] = []
    for el in nodes:
        if not isinstance(el, Tag):
            continue
        if el.name in ("script", "style", "noscript"):
            continue
        t = _text(el)
        if not t:
            continue
        if any(t.lower().startswith(p.lower()) for p in ["how to use", "how to take"]):
            break
        # If the node contains "How to use" later, trim at that
        t = _truncate_at_phrases(t, ["How to use", "How to take"])
        if t:
            parts.append(t)

    text = _clean_ws(" ".join(parts))
    text = _compress_repeat_runs(text)
    text = _dedupe_sentences(text)

    # Prefer UL after "Common side effects"
    effects: List[str] = []

    common_anchor: Optional[Tag] = None
    for el in nodes:
        if not isinstance(el, Tag):
            continue
        if el.name in ("script", "style", "noscript"):
            continue
        t = _text(el)
        if t.lower().startswith("common side effects"):
            common_anchor = el
            break

    if common_anchor:
        ul = common_anchor.find_next("ul")
        if ul:
            effects = [_text(li) for li in ul.find_all("li")]
            effects = [x for x in effects if x]

    # Fallback: first UL inside the section
    if not effects:
        for ul in [n for n in nodes if isinstance(n, Tag) and n.name == "ul"]:
            effects = [_text(li) for li in ul.find_all("li")]
            effects = [x for x in effects if x]
            if effects:
                break

    # Final cleanup (preserve parenthetical text inside li)
    effects = _dedupe_preserve_order([_compress_repeat_runs(e) for e in effects])

    return text, effects


def _extract_quick_tips(soup: BeautifulSoup) -> List[str]:
    for tag in soup.find_all(["h2", "h3", "h4", "h5"]):
        t = _text(tag).lower()
        if "quick tips" in t or t.startswith("quick tip"):
            nodes = _section_nodes(tag)
            for ul in [n for n in nodes if isinstance(n, Tag) and n.name == "ul"]:
                tips = [_text(li) for li in ul.find_all("li")]
                tips = [x for x in tips if x]
                if tips:
                    return _dedupe_preserve_order(tips)
            break
    return []


def _extract_fact_box(soup: BeautifulSoup) -> Tuple[Dict[str, str], Dict[str, str], List[Dict[str, str]]]:
    """
    Fact box rows look like:
      <div class="DrugFactBox__fact-row___...">
         <div class="DrugFactBox__col-left___...">Chemical Class</div>
         <div class="DrugFactBox__col-right___...">Peptides</div>
      </div>
    """
    h = None
    for tag in soup.find_all(["h2", "h3", "h4", "h5"]):
        if "fact box" in _text(tag).lower():
            h = tag
            break
    if not h:
        return {}, {}, []

    nodes = _section_nodes(h)
    fact: Dict[str, str] = {}

    rows: List[Tag] = []
    for n in nodes:
        if isinstance(n, Tag) and n.get("class") and any("fact-row" in c for c in n.get("class", [])):
            rows.append(n)

    for r in rows:
        cols = r.find_all("div", recursive=False)
        if len(cols) >= 2:
            k = _text(cols[0])
            v = _text(cols[1])
            if k and v and k not in fact:
                fact[k] = v

    core_keys = {"Chemical Class", "Habit Forming", "Therapeutic Class", "Action Class"}
    core = {k: fact[k] for k in core_keys if k in fact}
    interactions = [{"title": k, "note": v} for k, v in fact.items() if k not in core_keys]

    return fact, core, interactions


def _extract_substitutes(soup: BeautifulSoup) -> List[Dict[str, str]]:
    # 1mg often uses "All substitutes" as the heading
    h = None
    for tag in soup.find_all(["h2", "h3", "h4", "h5"]):
        if "substitut" in _text(tag).lower():
            h = tag
            break
    if not h:
        return []

    nodes = _section_nodes(h)
    subs: List[Dict[str, str]] = []
    for a in [n for n in nodes if isinstance(n, Tag) and n.name == "a" and n.get("href")]:
        href = a["href"]
        if isinstance(href, str) and href.startswith("/drugs/"):
            text = _text(a)
            if text and not text.lower().startswith("view all"):
                subs.append({"text": text, "href": href})

    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    for s in subs:
        if s["href"] not in seen:
            seen.add(s["href"])
            out.append(s)
    return out


def _parse_marketer_details_block(text: str) -> Dict[str, str]:
    """
    Fix:
    - Some pages omit colons for labels.
    - Trim noisy tails in "Expires on or after" (often includes delivery/prescription boilerplate).
    """
    text = _clean_ws(text)
    if not text:
        return {}

    keys = [
        ("Name", r"\bName\b"),
        ("Country of origin", r"\bCountry of origin\b"),
        ("Expires on or after", r"\bExpires on or after\b"),
        ("Email", r"\bEmail(?: ID)?\b"),
        ("Phone", r"\bPhone(?: Number)?\b"),
        ("Address", r"\bAddress\b"),
    ]

    hits: List[Tuple[int, int, str]] = []
    for canon, pat in keys:
        for m in re.finditer(pat, text, flags=re.I):
            hits.append((m.start(), m.end(), canon))

    if not hits:
        return {"raw": text}

    hits.sort(key=lambda x: x[0])
    out: Dict[str, str] = {}

    for i, (s, e, canon) in enumerate(hits):
        if i > 0 and s < hits[i - 1][1]:
            # overlapping match (e.g., Email inside Email ID)
            continue
        j = e
        while j < len(text) and text[j] in ": -":
            j += 1
        end = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        val = text[j:end].strip()
        if not val:
            continue

        # Clean common pollution in Expires field
        if canon == "Expires on or after":
            # Keep leading date-ish portion; drop boilerplate tail.
            # e.g. "January, 2028 A licensed vendor partner ..." -> "January, 2028"
            val2 = re.split(r"\bA licensed vendor partner\b|\bOnce the pharmacy\b|\bAcceptance of your order\b", val, flags=re.I)[
                0
            ].strip()
            # If still too long, cut at first strong sentence boundary.
            if len(val2) > 60 and "." in val2:
                val2 = val2.split(".", 1)[0].strip()
            val = val2

        # Address sanity: sometimes Address picks up "Country ..." due to slicing weirdness
        if canon == "Address" and val.lower().startswith("country of origin"):
            val = re.sub(r"(?i)^country of origin\s*", "", val).strip()

        if val and canon not in out:
            out[canon] = val

    return out


def _extract_marketer_details(soup: BeautifulSoup) -> Dict[str, str]:
    h = _find_heading(soup, "marketer details")
    if not h:
        return {}

    nodes = _section_nodes(h)
    parts: List[str] = []
    for el in nodes:
        if isinstance(el, Tag) and el.name not in ("script", "style", "noscript"):
            t = _text(el)
            if not t:
                continue
            # Stop at obvious next-section boundary if embedded
            if any(t.lower().startswith(bp.lower()) for bp in _BOUNDARY_PHRASES) and len(t) <= 60:
                break
            parts.append(t)

    combined = _clean_ws(" ".join(parts))
    combined = _truncate_at_phrases(combined, _STOP_AT_PHRASES)
    combined = _compress_repeat_runs(combined)
    return _parse_marketer_details_block(combined)


def _extract_patient_concerns(soup: BeautifulSoup, faq_json: Optional[dict]) -> List[Dict[str, str]]:
    """
    Fix: Use the FAQPage JSON-LD when available (clean Q/A, no doctor-name spam).
    DOM fallback pairs divs with class containing "question" and "answer".
    """
    if faq_json and isinstance(faq_json.get("mainEntity"), list):
        out: List[Dict[str, str]] = []
        for ent in faq_json["mainEntity"]:
            if not isinstance(ent, dict):
                continue
            q = _strip_html(str(ent.get("name") or ""))
            a = ""
            ans = ent.get("acceptedAnswer")
            if isinstance(ans, dict):
                a = _strip_html(str(ans.get("text") or ""))
            if q and a:
                out.append({"question": q, "answer": a})
        return out

    # DOM fallback
    h = _find_heading(soup, "patient concerns")
    if not h:
        return []

    nodes = _section_nodes(h)
    questions: List[str] = []
    answers: List[str] = []

    for el in nodes:
        if not isinstance(el, Tag):
            continue
        cls = " ".join(el.get("class", []))
        t = _text(el)
        if not t:
            continue
        if "question" in cls.lower():
            if not t.endswith("?"):
                t = t.rstrip("?") + "?"
            questions.append(t)
        elif "answer" in cls.lower():
            answers.append(t)

    out: List[Dict[str, str]] = []
    for q, a in zip(questions, answers):
        out.append({"question": q, "answer": a})
    return out


# -----------------------
# Public API
# -----------------------
def parse_1mg_rendered_html(html: str, url: Optional[str] = None, http_status: Optional[int] = None) -> Dict[str, Any]:
    """
    Parse a 1mg rendered HTML page into the JSON shape you've standardized.

    Inputs:
      - html: rendered HTML string (Playwright output preferred)
      - url: optional canonical URL
      - http_status: optional HTTP status from the fetch layer

    Output fields match your latest JSON outputs.
    """
    soup = BeautifulSoup(html, "lxml")
    drug_json, faq_json = _parse_jsonld(soup)
    header_meta = _extract_header_meta(soup)

    # Core fields
    name = (drug_json or {}).get("name") if drug_json else None
    if not name:
        h1 = soup.find("h1")
        name = _text(h1) if isinstance(h1, Tag) else None

    # prescription_required
    prescription_required = False
    if drug_json:
        ps = drug_json.get("prescriptionStatus") or drug_json.get("legalStatus") or ""
        prescription_required = "prescription" in str(ps).lower()
    else:
        # fallback: if header meta contains "Prescription Required"
        for k, v in header_meta.items():
            if k.lower().startswith("prescription"):
                prescription_required = "required" in v.lower() or v.lower() in ("yes", "true")
                break

    # marketer
    marketer = None
    if drug_json:
        mk = drug_json.get("marketer") or drug_json.get("manufacturer")
        if isinstance(mk, dict):
            marketer = mk.get("legalName") or mk.get("name")
        elif isinstance(mk, str):
            marketer = mk
    if not marketer:
        # accommodate a few variants
        marketer = header_meta.get("Marketer") or header_meta.get("marketer")

    # salt composition
    salt_composition = (
        header_meta.get("Salt Composition")
        or header_meta.get("SALT COMPOSITION")
        or header_meta.get("Salt composition")
    )
    if not salt_composition and drug_json:
        ing = drug_json.get("activeIngredient")
        strength = _format_strength(drug_json.get("availableStrength"))
        if ing:
            ing_name = ing[0] if isinstance(ing, list) else ing
            salt_composition = f"{ing_name} ({strength})" if strength else str(ing_name)

    # storage
    storage = header_meta.get("Storage") or header_meta.get("storage") or ""

    # content sections
    product_information = _extract_product_information(soup, drug_json)
    uses = _extract_uses(soup)
    benefits, benefits_by_use = _extract_benefits(soup)
    side_effects, side_effects_list = _extract_side_effects(soup)
    quick_tips_or_facts = _extract_quick_tips(soup)
    fact_box, fact_box_core, fact_box_interactions = _extract_fact_box(soup)
    substitutes = _extract_substitutes(soup)
    patient_concerns = _extract_patient_concerns(soup, faq_json)
    marketer_details = _extract_marketer_details(soup)

    # Final safety: ensure no nav-dump slipped through in product_information
    if _looks_like_nav_noise(product_information):
        product_information = ""

    return {
        "url": url or ((drug_json or {}).get("url") if drug_json else None),
        "name": name,
        "prescription_required": prescription_required,
        "marketer": marketer,
        "salt_composition": salt_composition,
        "storage": storage,
        "product_information": product_information,
        "uses": uses,
        "benefits": benefits,
        "benefits_by_use": benefits_by_use,
        "side_effects": side_effects,
        "side_effects_list": side_effects_list,
        "quick_tips_or_facts": quick_tips_or_facts,
        "fact_box": fact_box,
        "fact_box_core": fact_box_core,
        "fact_box_interactions": fact_box_interactions,
        "substitutes": substitutes,
        "drug_interactions": "",  # keep for schema consistency (1mg often doesn't render a dedicated section)
        "patient_concerns": patient_concerns,
        "marketer_details": marketer_details,
        "http_status": http_status,
    }


def parse_drug_page(html: str, url: str | None = None, http_status: int | None = None) -> dict:
    """
    Backward-compatible wrapper used by the rest of the codebase.
    """
    return parse_1mg_rendered_html(html=html, url=url, http_status=http_status)