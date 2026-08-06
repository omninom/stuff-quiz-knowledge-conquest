#!/usr/bin/env python3
"""
Batch-verify correct answers for every quiz currently in the cache (or a
given list of riddle IDs), using real headless-browser playthroughs
(riddle_verify.verify_riddle_answers), and write the verified answers back
into cache/quiz_cache.json.

Runs multiple quizzes concurrently (separate browser instances per worker)
since each playthrough is mostly a matter of waiting on network/animation,
not CPU. Safe to re-run: quizzes already marked "verified" are skipped
unless --force is passed.

Usage:
    python verify_cache.py            # verify everything unverified
    python verify_cache.py --force    # re-verify everything
    python verify_cache.py --workers 8
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from playwright.sync_api import sync_playwright

import quiz_scraper as qs
from riddle_verify import capture_riddle_images, verify_riddle_answers


def verify_one(qid: str, quiz: dict) -> tuple:
    """Runs in a worker thread: launches its own browser, plays through the
    quiz, and returns (qid, updated_questions, verified_count, total_count)."""
    riddle_id = quiz.get("riddle_id")
    questions = quiz.get("questions", [])
    if not riddle_id or not questions:
        return qid, questions, 0, len(questions)

    # Single-option questions (e.g. "The Hard Word" anagram puzzles, which
    # list only the one accepted answer with no distractors) are trivially
    # unambiguous — there's nothing for a live playthrough to disambiguate,
    # so mark them verified without spinning up a browser for them.
    needs_browser = [q for q in questions if len(q.get("options", [])) > 1]
    for q in questions:
        if len(q.get("options", [])) == 1:
            q["verified"] = True
    if not needs_browser:
        return qid, questions, len(questions), len(questions)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            answer_map = verify_riddle_answers(page, riddle_id)
            browser.close()
    except Exception as e:
        print(f"  [{qid}] ERROR launching browser: {e}", file=sys.stderr)
        return qid, questions, 0, len(questions)

    verified_count = 0
    for q in questions:
        result = answer_map.get(q["question"])
        correct = result.get("answer") if result else None
        if correct and correct in q["options"]:
            q["correct_answer"] = correct
            q["verified"] = True
            verified_count += 1
        else:
            q["verified"] = False
        if result and result.get("image_url"):
            q["image_url"] = result["image_url"]

    return qid, questions, verified_count, len(questions)


def backfill_images_one(qid: str, quiz: dict) -> tuple:
    """Runs in a worker thread: like verify_one, but only collects each
    question's image URL (via the faster capture_riddle_images, which
    skips waiting on the answer-reveal state) — for quizzes whose answers
    are already verified and just need images added. Returns
    (qid, updated_questions, images_added, total_count)."""
    riddle_id = quiz.get("riddle_id")
    questions = quiz.get("questions", [])
    needs_browser = [q for q in questions if len(q.get("options", [])) > 1]
    if not riddle_id or not needs_browser:
        return qid, questions, 0, len(questions)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            image_map = capture_riddle_images(page, riddle_id)
            browser.close()
    except Exception as e:
        print(f"  [{qid}] ERROR launching browser: {e}", file=sys.stderr)
        return qid, questions, 0, len(questions)

    added = 0
    for q in questions:
        url = image_map.get(q["question"])
        if url and not q.get("image_url"):
            q["image_url"] = url
            added += 1

    return qid, questions, added, len(questions)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=6, help="number of quizzes to verify in parallel")
    ap.add_argument("--force", action="store_true", help="re-verify quizzes that are already marked verified")
    ap.add_argument("--limit", type=int, default=None, help="cap number of quizzes processed (for testing)")
    ap.add_argument(
        "--images-only",
        action="store_true",
        help="skip answer verification; just backfill image_url onto already-verified questions that lack one",
    )
    args = ap.parse_args()

    cache = qs._load_cache()

    if args.images_only:
        run_fn = backfill_images_one
        todo = []
        for qid, quiz in cache.items():
            questions = quiz.get("questions")
            if not questions or not quiz.get("riddle_id"):
                continue
            missing_images = [q for q in questions if len(q.get("options", [])) > 1 and not q.get("image_url")]
            if not missing_images:
                continue
            todo.append(qid)
        noun = "images added"
    else:
        run_fn = verify_one
        todo = []
        for qid, quiz in cache.items():
            if not quiz.get("questions") or not quiz.get("riddle_id"):
                continue
            already_done = all(q.get("verified") for q in quiz["questions"])
            if already_done and not args.force:
                continue
            todo.append(qid)
        noun = "verified"

    todo.sort(key=lambda x: int(x))
    if args.limit:
        todo = todo[: args.limit]

    total = len(todo)
    action = "Backfilling images for" if args.images_only else "Verifying"
    print(f"{action} {total} quizzes with {args.workers} parallel workers...", file=sys.stderr)
    if total == 0:
        print("Nothing to do.", file=sys.stderr)
        return

    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(run_fn, qid, cache[qid]): qid for qid in todo}
        for future in as_completed(futures):
            qid = futures[future]
            done += 1
            try:
                qid, questions, count, total_count = future.result()
                cache[qid]["questions"] = questions
                elapsed = time.time() - t0
                eta = (elapsed / done) * (total - done)
                print(
                    f"[{done}/{total}] {qid}: {count}/{total_count} {noun} "
                    f"(elapsed {elapsed:.0f}s, eta {eta:.0f}s)",
                    file=sys.stderr,
                )
            except Exception as e:
                print(f"[{done}/{total}] {qid}: FAILED — {e}", file=sys.stderr)

            if done % 5 == 0:
                qs._save_cache()

    qs._save_cache()
    print(f"Done in {time.time()-t0:.0f}s. Cache saved to {qs.CACHE_FILE}", file=sys.stderr)


if __name__ == "__main__":
    main()
