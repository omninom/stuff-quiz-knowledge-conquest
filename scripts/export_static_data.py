#!/usr/bin/env python3
"""
Export the working scrape cache (cache/quiz_cache.json, maintained by
quiz_scraper.py) into a single flattened, filtered JSON bundle that the
static site (site/) fetches directly at runtime.

Only two things ever make it into the exported bundle:
  1. Questions whose correct answer has been ground-truth verified via a
     live headless-browser playthrough (riddle_verify.py).
  2. Questions belonging to a series still in PICKLIST (Morning/Afternoon
     Trivia). Kids Trivia is excluded defensively even if stale entries
     remain in the raw working cache (see quiz_scraper.PICKLIST docstring).

The output intentionally flattens "quiz -> questions" into one flat list of
question records, each carrying its own source metadata, so the frontend
(site/app.js) can filter/sample it directly with no extra grouping logic.

Usage:
    python scripts/export_static_data.py [output_path]

Default output_path is site/data/questions.json (relative to the repo root).
"""

import json
import os
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import quiz_scraper as qs  # noqa: E402

DEFAULT_OUTPUT = os.path.join(REPO_ROOT, "site", "data", "questions.json")
ALLOWED_SERIES_KEYS = {opt["key"] for opt in qs.PICKLIST}


def build_bundle():
    cache = qs._load_cache()

    questions = []
    for quiz in cache.values():
        series_key = quiz.get("series_key")
        if series_key is None:
            _, series_key = qs.series_for_slug(quiz.get("url", ""), quiz.get("title", ""))
        if series_key not in ALLOWED_SERIES_KEYS:
            continue

        for q in quiz.get("questions", []):
            if not q.get("verified"):
                continue
            questions.append(
                {
                    "question": q["question"],
                    "options": q["options"],
                    "correct_answer": q["correct_answer"],
                    "image_url": q.get("image_url"),
                    "series_key": series_key,
                    "source": {
                        "title": quiz.get("title"),
                        "date": quiz.get("date"),
                        "series": quiz.get("series"),
                        "url": quiz.get("url"),
                    },
                }
            )

    dates = [q["source"]["date"] for q in questions if q["source"]["date"]]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "series": qs.PICKLIST,
        "stats": {
            "total_questions": len(questions),
            "earliest": min(dates) if dates else None,
            "latest": max(dates) if dates else None,
        },
        "questions": questions,
    }


def export(output_path: str = DEFAULT_OUTPUT):
    bundle = build_bundle()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False)
    print(
        f"Exported {bundle['stats']['total_questions']} verified questions "
        f"({bundle['stats']['earliest']} to {bundle['stats']['latest']}) -> {output_path}",
        file=sys.stderr,
    )
    return bundle


if __name__ == "__main__":
    export(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUTPUT)
