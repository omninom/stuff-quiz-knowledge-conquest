# Stuff Quiz Knowledge Conquest

A small web app that generates a custom trivia quiz from Stuff.co.nz's daily
quizzes. Pick a date range, how many questions you want, and which quiz
types to draw from (currently Morning / Afternoon / Kids Trivia) — it
randomly samples questions from that pool and runs an interactive quiz with
scoring. The quiz-taking screen is styled to match riddle.com's own quiz
player (colors, fonts, the correct/wrong reveal pills, per-question header
photos, and the image fade-in transition) — see "How it works" below.

## How it works

1. **Discovery** — [Wayback Machine CDX](https://web.archive.org/cdx/search/cdx)
   is used to find Stuff quiz article IDs published in a given date range
   (Stuff's own site only exposes the ~50 most recent items per topic).
2. **Content** — each article's questions/options come from Stuff's public
   `https://www.stuff.co.nz/api/v1.0/stuff/story/<id>` API, which includes a
   no-JS HTML fallback of the embedded Riddle.com quiz widget.
3. **Answer verification** — Riddle's static data deliberately withholds
   the correct answer (it's revealed client-side only after answering), and
   naive heuristics based on the raw option order turned out to sometimes be
   **wrong** (see `riddle_verify.py`'s docstring for a concrete example).
   So every question's correct answer is confirmed by actually playing
   through the live quiz once in headless Chromium (Playwright) and reading
   the "Correct answer" tag Riddle shows after answering. This is slow
   (~1–3s per question) but the result is cached forever, and the app only
   ever serves questions that have been verified this way.
4. **Images** — each question in a Riddle quiz has its own header photo,
   served from `cdn.riddle.com` with no auth/referrer restrictions (so it
   can be hotlinked directly). These aren't exposed by Stuff's plain story
   API, so `riddle_verify.py` captures each question's image URL during the
   same headless-browser playthrough used for answer verification. The
   frontend preloads the *next* question's image while the current one is
   still on screen (see `static/app.js`), so by the time you click "Next"
   it's normally already cached and fades in immediately — matching
   riddle.com's own `opacity 0.5s ease-out` reveal instead of trailing a
   live fetch. Questions without a captured image yet fall back to a plain
   gradient banner rather than showing nothing.
5. Everything is cached in `cache/quiz_cache.json` so repeat requests for an
   overlapping date range are instant.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## Running

```bash
source venv/bin/activate
python app.py
```

Then open http://127.0.0.1:5001

The cache is seeded on first run from `../stuff_quizzes.json` (the output of
the standalone `scrape_stuff_quizzes.py` script one level up), if present.

## Re-verifying / backfilling the cache offline

Generating a quiz for a brand-new date range triggers scraping *and*
verification inline (in parallel, up to 8 quizzes at once), which can take
a while for a big, never-seen-before range. To pre-warm the cache instead of
waiting on a request:

```bash
source venv/bin/activate
python verify_cache.py --workers 8
```

This verifies any quiz in the cache that isn't fully verified yet (safe to
re-run; already-verified quizzes are skipped unless you pass `--force`).

To backfill images onto questions that were answer-verified *before* image
capture existed (skips the "Correct answer" reveal-wait, so it's noticeably
faster than a full re-verify):

```bash
python verify_cache.py --images-only --workers 8
```

Image capture in particular is best-effort per playthrough (see "Known
limitations"), so re-running `--images-only` a few times tends to keep
picking up stragglers it missed the first time — check current coverage any
time via `GET /api/stats`.

**Don't run `verify_cache.py` at the same time as generating quizzes that
require fresh scraping** — both processes independently load/save
`cache/quiz_cache.json`, and whichever finishes saving last will clobber the
other's progress.

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask app + API (`/api/generate`, `/api/stats`, `/api/series`) |
| `quiz_scraper.py` | Discovery (Wayback CDX) + content fetch (Stuff API) + caching |
| `riddle_verify.py` | Headless-browser ground-truth answer verification + image capture |
| `verify_cache.py` | CLI to batch-verify/backfill the whole cache in parallel |
| `templates/`, `static/` | Frontend (server-rendered shell + vanilla JS) |
| `cache/quiz_cache.json` | Persistent cache of scraped + verified quizzes |

## Known limitations

- A small number of quizzes mix multiple-choice questions with free-text
  "TextEntry" blocks (e.g. some Weekender Trivia editions). The verifier
  only knows how to play through radio-button questions, so a TextEntry
  block partway through a quiz can cause the remaining questions in that
  quiz to be skipped (marked unverified, and excluded from the app's
  question pool) rather than mis-verified.
- Image capture is separately best-effort: a playthrough can be interrupted
  by an interstitial ad screen or similar UI transition before it reaches
  every question, so coverage fills in gradually across repeated
  `--images-only` runs rather than hitting 100% on the first pass.
- A tiny fraction of quizzes use a non-standard/visual format (e.g.
  image-matching) with no text fallback at all and are skipped entirely.
- Per Stuff's `robots.txt`, several AI-crawler user agents (including
  ClaudeBot) are disallowed site-wide. This app doesn't identify as any of
  those, but keep usage personal/low-volume out of respect for that stated
  preference.

## Goals / roadmap

- **Bring back Weekender Trivia.** It's deliberately left out of the
  picklist for now (see `quiz_scraper.PICKLIST`) because its editions mix in
  free-text "TextEntry" blocks that the verifier can't play through, which
  currently aborts verification for the rest of that quiz too. Making the
  verifier skip a lone TextEntry block and keep going (rather than bailing
  out) would let Weekender come back with good coverage.
- **Expose the other quiz series the scraper already recognizes.**
  `SERIES_RULES` in `quiz_scraper.py` already tags "The Hard Word", "Really
  Hard Word", and "News Quiz" — and single-option Hard Word questions are
  already auto-verified with no browser needed (see `verify_cache.verify_one`)
  — but none of them are in `PICKLIST` yet, so they're never offered as a
  selectable quiz type in the UI.
- **Improve image-capture coverage** beyond the current best-effort
  playthrough, e.g. by making `_advance_to_new_question` more robust to
  whatever interstitial/ad-transition states are causing playthroughs to
  stop partway through a quiz.
- **Handle non-standard visual quiz formats** (e.g. image-matching) instead
  of skipping them outright, if a text-based fallback can be found.
- Longer-term/lower priority: swap the Flask dev server for a real WSGI
  server if this ever needs to run somewhere besides localhost.
