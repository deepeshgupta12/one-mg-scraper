# one-mg-scraper

A small, practical scraper for **1mg drug detail pages** that produces a clean, normalized dataset in **JSONL** and **CSV**.

> **Note on compliance:** Always review 1mg’s Terms of Use and applicable laws before scraping. Use respectful rate limits and avoid unnecessary load.

---

## What this repo does

Given a list of 1mg drug URLs, the scraper:
1. Opens each URL using Playwright (headed or headless).
2. Handles blocks/challenges (optional manual mode).
3. Extracts structured fields (name, marketer, salt composition, storage, uses, benefits, side effects, fact box, substitutes, FAQ/patient concerns, marketer details).
4. Writes outputs to:
   - `data/output/medicines.jsonl` (one JSON object per line)
   - `data/output/medicines.csv` (tabular form)

---

## Output schema (high level)

Each record includes (subset shown):

```json
{
  "url": "https://www.1mg.com/drugs/actorise-40-injection-227647",
  "name": "Actorise 40 Injection",
  "prescription_required": true,
  "marketer": "Cipla Ltd",
  "salt_composition": "Darbepoetin alfa (40mcg)",
  "storage": "Store in a refrigerator (2 - 8°C). Do not freeze.",
  "product_information": "...",
  "uses": ["..."],
  "benefits": "...",
  "benefits_by_use": [{"use": "...", "text": "..."}],
  "side_effects": "...",
  "side_effects_list": ["..."],
  "quick_tips_or_facts": ["..."],
  "fact_box": {"Chemical Class": "...", "Habit Forming": "..."},
  "fact_box_core": {"Chemical Class": "...", "Habit Forming": "..."},
  "fact_box_interactions": [],
  "substitutes": [{"text": "...", "href": "/drugs/..."}],
  "drug_interactions": "",
  "patient_concerns": [{"question": "...", "answer": "..."}],
  "marketer_details": {"Name": "...", "Address": "...", "Country of origin": "..."},
  "http_status": 200
}
```

---

## Repo layout (typical)

```text
one-mg-scraper/
  src/
    one_mg_scraper/
      cli.py
      parser.py
      fetcher.py
      ...
  data/
    urls/
      urls.jsonl
    output/
      medicines.jsonl
      medicines.csv
  pyproject.toml / requirements.txt
  README.md
```

> Your exact layout may differ slightly depending on how you organized modules, but the usage below stays the same.

---

## Setup

### 1) Clone & create a virtualenv

```bash
git clone https://github.com/deepeshgupta12/one-mg-scraper.git
cd one-mg-scraper

python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

### 2) Install dependencies

If you use `requirements.txt`:

```bash
pip install -r requirements.txt
```

If you use `pyproject.toml`:

```bash
pip install -e .
```

### 3) Install Playwright browsers

```bash
python -m playwright install
```

---

## Input URLs

Put URLs in a **JSONL** file like:

`data/urls/urls.jsonl`

```jsonl
{"url": "https://www.1mg.com/drugs/actorise-40-injection-227647"}
{"url": "https://www.1mg.com/drugs/avastin-100mg-injection-135666"}
```

---

## Run the scraper

Example command (detail scraping):

```bash
python -m src.one_mg_scraper.cli scrape-details \
  --urls data/urls/urls.jsonl \
  --out-jsonl data/output/medicines.jsonl \
  --out-csv data/output/medicines.csv \
  --concurrency 1 \
  --delay 3.0 \
  --limit 2 \
  --headed \
  --manual-challenge \
  --profile data/profile
```

### Flags (what they mean)
- `--concurrency`: number of pages processed in parallel (start with 1)
- `--delay`: polite delay between navigations/actions (seconds)
- `--limit`: stop after N URLs (useful for testing)
- `--headed`: run Playwright with a visible browser
- `--manual-challenge`: pause when the site shows blocks/captcha so you can solve manually
- `--profile`: Playwright user-data-dir so sessions/cookies persist

---

## Parser notes

The parser is designed to be robust to 1mg’s changing DOM:
- Uses **JSON-LD** (`Drug`, `FAQPage`) when present for clean structured fields.
- Uses DOM section parsing with heading-aware boundaries.
- Extracts:
  - benefits in structured blocks (by “In <use> …” subheadings)
  - side effects list from the “Common side effects …” section (prefers UL)
  - fact box from fact-row style blocks
  - substitutes from `/drugs/...` links inside substitutes section

---

## Development workflow

### Run a quick smoke test

```bash
python -m src.one_mg_scraper.cli scrape-details \
  --urls data/urls/urls.jsonl \
  --out-jsonl /tmp/meds.jsonl \
  --out-csv /tmp/meds.csv \
  --concurrency 1 \
  --delay 3.0 \
  --limit 1 \
  --headed \
  --manual-challenge \
  --profile data/profile
```

### Lint / format (optional)

If you use `ruff` and `black`:

```bash
ruff check .
black .
```

---

## Troubleshooting

**1) “Blocked/captcha”**
- Run with `--headed --manual-challenge` and re-run the same URL.
- Use a persistent `--profile` directory to keep cookies.

**2) Missing sections**
- Some drugs don’t render every section. JSON-LD fallback helps.
- If a page is heavily JS-driven, ensure Playwright waits for network idle or a stable selector.

**3) CSV columns**
- Nested fields (lists/dicts) may be JSON-stringified in CSV for consistency.

---

## License

Add a license if you plan to publish or distribute. Until then, treat this repo as internal tooling.

---

## Disclaimer

This project is for educational/internal product research purposes. You are responsible for how you use it and ensuring compliance with the target website’s policies and local regulations.
