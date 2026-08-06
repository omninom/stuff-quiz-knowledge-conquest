"""
Importable scraping/caching layer for the Stuff.co.nz quiz generator app.

This builds on the technique from ../scrape_stuff_quizzes.py (Wayback
Machine CDX to discover article IDs, Stuff's public story API to fetch
question/option text) but reworked as a module with:

  * an explicit from_date/to_date range (instead of "last N days")
  * a persistent on-disk cache (cache/quiz_cache.json) so repeat requests
    for overlapping date ranges don't re-hit the network
  * a series pre-filter (using the URL slug) so we only fetch full story
    content for quiz types the caller actually wants
  * ground-truth answer verification via riddle_verify.py — the raw scraped
    option order is NOT reliable on its own (see that module's docstring),
    so every question is also played through live in headless Chromium

See the top-level scrape_stuff_quizzes.py docstring for background on the
overall approach, including the robots.txt / AI-crawler caveat.
"""

import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
STORY_API = "https://www.stuff.co.nz/api/v1.0/stuff/story/{id}"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_DELAY = 0.5  # be polite to Stuff's API between story fetches

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "quiz_cache.json")
SEED_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stuff_quizzes.json")

QUIZ_URL_RE = re.compile(r"/quizzes/(\d+)/([a-z0-9\-]+)", re.IGNORECASE)
SECTION_RE = re.compile(r'<section data-block="([^"]+)">(.*?)</section>', re.S)
HEADING_RE = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.S)
LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.S)
TAG_RE = re.compile(r"<[^>]+>")

# (slug substring, display label, picklist key). Order matters: first match wins.
SERIES_RULES = [
    ("morning-trivia", "Morning Trivia", "morning"),
    ("afternoon-trivia", "Afternoon Trivia", "afternoon"),
    ("kids-trivia", "Kids Trivia", "kids"),
    ("weekender-trivia", "Weekender Trivia", "weekender"),
    ("really-hard-word", "Really Hard Word", "really_hard_word"),
    ("hard-word", "The Hard Word", "hard_word"),
    ("news-quiz", "News Quiz", "news"),
]

# The picklist exposed to the user in the UI.
# Weekender Trivia is deliberately excluded for now: its editions have too
# much structural variation (mixed TextEntry blocks, non-standard/visual
# formats) which trips up verification (see README "Known limitations").
# It's still recognized by SERIES_RULES above so any that slip into the
# cache are labeled correctly, just not offered as a selectable option.
PICKLIST = [
    {"key": "morning", "label": "Morning Trivia"},
    {"key": "afternoon", "label": "Afternoon Trivia"},
    {"key": "kids", "label": "Kids Trivia"},
]

_cache_lock = threading.Lock()
_cache = None  # loaded lazily


def strip_tags(s: str) -> str:
    return html.unescape(TAG_RE.sub("", s)).strip()


def series_for_slug(slug: str, title: str = ""):
    text = f"{slug} {title}".lower()
    for needle, label, key in SERIES_RULES:
        if needle in text:
            return label, key
    return "Other/Special", "other"


def http_get(url: str, timeout: int = 20, retries: int = 3, backoff: float = 1.5) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            last_err = e
            time.sleep(backoff * (attempt + 1))
    raise last_err


def _load_cache():
    global _cache
    if _cache is not None:
        return _cache
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            _cache = json.load(f)
    elif os.path.exists(SEED_FILE):
        # Seed from the already-scraped month of data produced earlier,
        # so the app is immediately useful without a fresh scrape.
        with open(SEED_FILE, "r", encoding="utf-8") as f:
            seed = json.load(f)
        _cache = {str(q["id"]): q for q in seed}
        _save_cache()
    else:
        _cache = {}
    return _cache


def _save_cache():
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_cache, f, ensure_ascii=False)
    os.replace(tmp, CACHE_FILE)


def discover_quiz_ids(from_date: date, to_date: date):
    """Query the Wayback Machine CDX API for Stuff quiz URLs published in
    [from_date, to_date]. Returns {id: {"url":..., "slug":...}}."""
    params = {
        "url": "www.stuff.co.nz/quizzes/",
        "matchType": "prefix",
        "output": "json",
        "collapse": "urlkey",
        "filter": "statuscode:200",
        "fl": "original,timestamp",
        "from": from_date.strftime("%Y%m%d"),
        "to": to_date.strftime("%Y%m%d"),
    }
    url = CDX_ENDPOINT + "?" + urllib.parse.urlencode(params)
    raw = http_get(url)
    rows = json.loads(raw.decode("utf-8", "replace"))
    if not rows:
        return {}
    rows = rows[1:]

    found = {}
    for original, ts in rows:
        m = QUIZ_URL_RE.search(original)
        if not m:
            continue
        qid, slug = m.group(1), m.group(2)
        if qid not in found:
            found[qid] = {"url": f"https://www.stuff.co.nz/quizzes/{qid}/{slug}", "slug": slug}
    return found


def parse_story(story_json: dict):
    content = story_json.get("content", {})
    title = content.get("title", "")
    story_date = story_json.get("publishedDate") or story_json.get("date")
    url = content.get("url", "")
    slug_m = re.search(r"/quizzes/\d+/([a-z0-9\-]+)", url)
    slug = slug_m.group(1) if slug_m else ""

    assets = content.get("contentBody", {}).get("assets", [])
    questions = []
    riddle_id = None
    for asset in assets:
        if asset.get("type") != "WIDGET":
            continue
        widget_html = asset.get("item", {}).get("content", "")
        if "data-block=" not in widget_html:
            continue
        rid_m = re.search(r'data-rid-id="([^"]+)"', widget_html)
        if rid_m:
            riddle_id = rid_m.group(1)
        for block_type, block_html in SECTION_RE.findall(widget_html):
            h_m = HEADING_RE.search(block_html)
            question_text = strip_tags(h_m.group(1)) if h_m else ""
            options = [strip_tags(li) for li in LI_RE.findall(block_html)]
            options = [o for o in options if o]
            if not question_text or len(options) < 2:
                continue
            questions.append(
                {
                    "type": block_type,
                    "question": question_text,
                    "options": options,
                    # Placeholder only — NOT trustworthy until riddle_verify
                    # confirms it (or verify_cache.py sets "verified": True
                    # for single-option questions, which are unambiguous).
                    # app.py only ever serves questions with verified=True.
                    "correct_answer": options[0],
                }
            )

    label, key = series_for_slug(slug, title)
    return {
        "id": story_json.get("id"),
        "title": title,
        "url": f"https://www.stuff.co.nz{url}" if url.startswith("/") else url,
        "date": story_date,
        "series": label,
        "series_key": key,
        "riddle_id": riddle_id,
        "num_questions": len(questions),
        "questions": questions,
    }


def fetch_quiz(qid: str):
    raw = http_get(STORY_API.format(id=qid))
    story_json = json.loads(raw.decode("utf-8", "replace"))
    return parse_story(story_json)


def ensure_range_cached(from_date: date, to_date: date, series_keys=None, progress_cb=None, verify=True):
    """Make sure every quiz published in [from_date, to_date] (optionally
    restricted to `series_keys`) is present in the on-disk cache, with
    ground-truth-verified correct answers. Fetches/verifies only what's
    missing. Returns the number of newly-fetched quizzes."""
    with _cache_lock:
        cache = _load_cache()
        ids = discover_quiz_ids(from_date, to_date)

        candidates = []
        for qid, info in ids.items():
            if qid in cache:
                continue
            if series_keys:
                _, key = series_for_slug(info["slug"])
                if key not in series_keys:
                    continue
            candidates.append(qid)

        fetched = 0
        newly_fetched_ids = []
        for i, qid in enumerate(candidates, 1):
            try:
                quiz = fetch_quiz(qid)
                cache[str(quiz["id"])] = quiz
                newly_fetched_ids.append(str(quiz["id"]))
                fetched += 1
            except Exception:
                pass  # skip failures silently; caller just gets fewer results
            if progress_cb:
                progress_cb(i, len(candidates))
            time.sleep(REQUEST_DELAY)

        if fetched:
            _save_cache()

    # Verification (headless-browser playthroughs) is done outside the cache
    # lock, in parallel, since each one is slow (~30-100s, dominated by
    # waiting on the live quiz's own animations/network) but doesn't touch
    # the cache dict structure itself until it returns.
    if verify and newly_fetched_ids:
        import verify_cache as vc
        from concurrent.futures import ThreadPoolExecutor, as_completed

        max_workers = min(4, len(newly_fetched_ids))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(vc.verify_one, qid, cache[qid]): qid for qid in newly_fetched_ids}
            for future in as_completed(futures):
                qid = futures[future]
                try:
                    _, questions, _, _ = future.result()
                    with _cache_lock:
                        cache[qid]["questions"] = questions
                except Exception:
                    pass
        with _cache_lock:
            _save_cache()

    return fetched


def get_cached_quizzes(from_date: date, to_date: date, series_keys=None):
    """Return cached quizzes whose publish date falls in [from_date, to_date],
    optionally filtered to the given series keys."""
    cache = _load_cache()
    out = []
    for quiz in cache.values():
        d = quiz.get("date")
        if not d:
            continue
        try:
            qdate = datetime.fromisoformat(d.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if not (from_date <= qdate <= to_date):
            continue
        key = quiz.get("series_key")
        if key is None:
            # Backfill series_key for quizzes that came from the old seed file.
            _, key = series_for_slug(quiz.get("url", ""), quiz.get("title", ""))
        if series_keys and key not in series_keys:
            continue
        if quiz.get("num_questions", 0) > 0:
            out.append(quiz)
    return out


def cache_stats():
    cache = _load_cache()
    dates = [q["date"] for q in cache.values() if q.get("date")]
    all_questions = [q for quiz in cache.values() for q in quiz.get("questions", [])]
    verified_questions = [q for q in all_questions if q.get("verified")]
    return {
        "total_quizzes": len(cache),
        "total_questions": len(all_questions),
        "verified_questions": len(verified_questions),
        "earliest": min(dates) if dates else None,
        "latest": max(dates) if dates else None,
    }
