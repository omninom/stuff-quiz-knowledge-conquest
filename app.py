"""
Flask app: generate a custom trivia quiz from Stuff.co.nz's daily quizzes.

Run with:
    source venv/bin/activate
    python app.py

Then open http://127.0.0.1:5001
"""

import random
from datetime import date, datetime

from flask import Flask, jsonify, render_template, request

import quiz_scraper as qs

app = Flask(__name__)

MAX_QUESTIONS = 50


def parse_date(value: str, fallback: date) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return fallback


@app.route("/")
def index():
    stats = qs.cache_stats()
    return render_template("index.html", picklist=qs.PICKLIST, stats=stats, max_questions=MAX_QUESTIONS)


@app.route("/api/series")
def api_series():
    return jsonify(qs.PICKLIST)


@app.route("/api/stats")
def api_stats():
    return jsonify(qs.cache_stats())


@app.route("/api/generate", methods=["POST"])
def api_generate():
    payload = request.get_json(force=True, silent=True) or {}

    today = date.today()
    start = parse_date(payload.get("start_date"), today.replace(day=1))
    end = parse_date(payload.get("end_date"), today)
    if start > end:
        start, end = end, start

    series_keys = payload.get("series") or [p["key"] for p in qs.PICKLIST]
    series_keys = [s for s in series_keys if s in {p["key"] for p in qs.PICKLIST}]
    if not series_keys:
        return jsonify({"error": "Select at least one quiz type."}), 400

    try:
        num_questions = int(payload.get("num_questions", 10))
    except (TypeError, ValueError):
        num_questions = 10
    num_questions = max(1, min(MAX_QUESTIONS, num_questions))

    # Make sure we have data for this range/series combo (fetches only what's
    # missing from the on-disk cache; a no-op if we've already got it).
    qs.ensure_range_cached(start, end, series_keys)

    quizzes = qs.get_cached_quizzes(start, end, series_keys)
    pool = []
    unverified_skipped = 0
    for quiz in quizzes:
        for q in quiz["questions"]:
            # Only ever serve questions whose correct answer has been
            # confirmed against a real playthrough of the live quiz — see
            # riddle_verify.py for why the raw scraped order can't be
            # trusted on its own.
            if not q.get("verified"):
                unverified_skipped += 1
                continue
            pool.append(
                {
                    "question": q["question"],
                    "options": q["options"],
                    "correct_answer": q["correct_answer"],
                    "image_url": q.get("image_url"),
                    "source_title": quiz["title"],
                    "source_date": quiz["date"],
                    "source_series": quiz["series"],
                    "source_url": quiz["url"],
                }
            )

    if not pool:
        return jsonify(
            {
                "error": (
                    "No verified questions found for that date range / quiz type combination. "
                    "Try widening the date range or selecting different quiz types, "
                    "or wait a moment if this range was just scraped (answer verification "
                    "runs shortly after)."
                )
            }
        ), 404

    sample_size = min(num_questions, len(pool))
    picked = random.sample(pool, sample_size)

    questions = []
    for item in picked:
        options = item["options"][:]
        random.shuffle(options)
        questions.append(
            {
                "question": item["question"],
                "options": options,
                "correct_answer": item["correct_answer"],
                "image_url": item.get("image_url"),
                "source": {
                    "title": item["source_title"],
                    "date": item["source_date"],
                    "series": item["source_series"],
                    "url": item["source_url"],
                },
            }
        )

    return jsonify(
        {
            "questions": questions,
            "requested": num_questions,
            "returned": len(questions),
            "pool_size": len(pool),
            "quizzes_used": len(quizzes),
            "unverified_skipped": unverified_skipped,
        }
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True, threaded=True)
