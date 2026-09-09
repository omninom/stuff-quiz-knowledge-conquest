#!/usr/bin/env python3
"""
Daily data-refresh entry point for the GitHub Actions workflow
(.github/workflows/update-and-deploy.yml).

1. Makes sure the last WINDOW_DAYS days of Morning/Afternoon Trivia are
   fetched + ground-truth verified in the working cache
   (cache/quiz_cache.json — downloaded from/re-uploaded to the `data-cache`
   GitHub Release by the workflow, not committed to git).
2. Exports the filtered, flattened static bundle the site actually serves
   (site/data/questions.json), via scripts/export_static_data.py.

A trailing multi-day window (rather than just "today") absorbs any Wayback
Machine CDX indexing lag without wasted work — ensure_range_cached() only
fetches/verifies quiz IDs that aren't already in the cache, so re-checking
already-covered days on every run is cheap.

Usage:
    python scripts/update_data.py [--window-days N] [--output PATH]
"""

import argparse
import os
import sys
from datetime import date, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import quiz_scraper as qs  # noqa: E402
from export_static_data import DEFAULT_OUTPUT, export  # noqa: E402

WINDOW_DAYS = 7
SERIES_KEYS = ["morning", "afternoon"]  # Kids Trivia dropped — see PICKLIST


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS,
                     help=f"how many trailing days to (re-)check for new quizzes (default: {WINDOW_DAYS})")
    ap.add_argument("--output", default=DEFAULT_OUTPUT,
                     help="where to write the exported static JSON bundle")
    args = ap.parse_args()

    today = date.today()
    start = today - timedelta(days=args.window_days)

    print(f"[update_data] ensuring {start} to {today} is cached+verified for {SERIES_KEYS} ...",
          file=sys.stderr)
    fetched = qs.ensure_range_cached(start, today, series_keys=SERIES_KEYS, verify=True)
    print(f"[update_data] fetched {fetched} new quizzes", file=sys.stderr)

    export(args.output)


if __name__ == "__main__":
    main()
