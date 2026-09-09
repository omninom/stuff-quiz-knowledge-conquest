#!/usr/bin/env python3
"""
Data-refresh entry point. Used two ways:

1. By the GitHub Actions workflow (.github/workflows/update-and-deploy.yml),
   daily, with no arguments — checks a trailing window of days for new
   Morning/Afternoon Trivia, verifies them, and exports the static bundle.
2. Locally, as the general-purpose "make sure this date range is
   scraped+verified" tool — e.g. to backfill an arbitrary range instead of
   waiting on the daily schedule. Pass --start-date/--end-date for that.

Either way it:
  a. Makes sure the requested range of Morning/Afternoon Trivia is fetched +
     ground-truth verified in the working cache (cache/quiz_cache.json — a
     GitHub Release asset in CI, a plain local file for local runs).
  b. Exports the filtered, flattened static bundle the site actually serves
     (site/data/questions.json), via scripts/export_static_data.py.

A trailing multi-day window (rather than just "today") absorbs any Wayback
Machine CDX indexing lag without wasted work — ensure_range_cached() only
fetches/verifies quiz IDs that aren't already in the cache, so re-checking
already-covered days on every run is cheap.

Usage:
    python scripts/update_data.py                                  # daily-run default: trailing 7 days
    python scripts/update_data.py --window-days 14                 # wider trailing window
    python scripts/update_data.py --start-date 2026-01-01 --end-date 2026-01-31   # explicit range (local backfill)
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import quiz_scraper as qs  # noqa: E402
from export_static_data import DEFAULT_OUTPUT, export  # noqa: E402

WINDOW_DAYS = 7
SERIES_KEYS = ["morning", "afternoon"]  # Kids Trivia dropped — see PICKLIST


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start-date", type=parse_date, help="explicit range start (YYYY-MM-DD); requires --end-date")
    ap.add_argument("--end-date", type=parse_date, help="explicit range end (YYYY-MM-DD); requires --start-date")
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS,
                     help=f"trailing days to (re-)check, if --start-date/--end-date aren't given (default: {WINDOW_DAYS})")
    ap.add_argument("--series", default=",".join(SERIES_KEYS),
                     help=f"comma-separated series keys to scrape (default: {','.join(SERIES_KEYS)})")
    ap.add_argument("--output", default=DEFAULT_OUTPUT,
                     help="where to write the exported static JSON bundle")
    args = ap.parse_args()

    if bool(args.start_date) != bool(args.end_date):
        ap.error("--start-date and --end-date must be given together")

    if args.start_date:
        start, end = args.start_date, args.end_date
    else:
        end = date.today()
        start = end - timedelta(days=args.window_days)

    series_keys = [s.strip() for s in args.series.split(",") if s.strip()]

    print(f"[update_data] ensuring {start} to {end} is cached+verified for {series_keys} ...",
          file=sys.stderr)
    fetched = qs.ensure_range_cached(start, end, series_keys=series_keys, verify=True)
    print(f"[update_data] fetched {fetched} new quizzes", file=sys.stderr)

    export(args.output)


if __name__ == "__main__":
    main()
