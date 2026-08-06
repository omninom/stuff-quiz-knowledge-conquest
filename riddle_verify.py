"""
Ground-truth answer verification for Riddle.com quizzes, using a real
headless browser.

Why this exists: Riddle's static embed JSON deliberately withholds the
correct answer (it's revealed only after a user submits any answer, via
client-side state populated at that point). An earlier heuristic ("the
first, un-shuffled option is always correct") worked on a couple of manually
spot-checked questions but turned out to be WRONG on others (confirmed:
"The tensioned cords used to stabilise tents ... are called ...?" is
"Guy ropes", not the alphabetically-first-looking "Guide ropes" — the
heuristic picked the wrong one). So instead we play through each quiz for
real in headless Chromium and read the "Correct answer" tag Riddle shows
after answering, which is the actual ground truth.

Riddle SPA quirks this has to work around (all discovered empirically by
screenshotting/instrumenting a real run):

  1. Clicking "Next" does not remove the previous question's radiogroup from
     the DOM — it just sits there (still matching `[role="radiogroup"]`,
     still "visible" per Playwright's definition) underneath whatever comes
     next. A naive "wait for a radiogroup to appear" check therefore
     succeeds *instantly* on every iteration, against the *stale* previous
     question, unless you explicitly check that its question id actually
     changed.
  2. Riddle sprinkles interstitial "Ad" blocks between questions. These have
     no radiogroup, just their own "Next" pill — and that pill can be
     rendered twice in the DOM (one enabled, one disabled, presumably for a
     transition/skip-delay effect), so blindly clicking ".first" sometimes
     hits the disabled copy and does nothing.

Both quirks are handled by `_advance_to_new_question`, which actively keeps
clicking any enabled "Next"-like button until a radiogroup with a genuinely
*different* question id shows up, rather than passively waiting.

This is much slower than the pure-HTTP approach (~1-3s per question) but the
result is cached forever per quiz once verified, so it's a one-time cost.
"""

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

RIDDLE_VIEW_URL = "https://www.riddle.com/view/{riddle_id}"
MAX_QUESTIONS_SAFETY = 40
MAX_ADVANCE_ATTEMPTS = 15
SETTLE_MS = 500


def _get_attribute_retry(locator, name, attempts=3, timeout=1500):
    for i in range(attempts):
        try:
            return locator.get_attribute(name, timeout=timeout)
        except PWTimeoutError:
            if i == attempts - 1:
                return None
            locator.page.wait_for_timeout(SETTLE_MS)


def _inner_text_retry(locator, attempts=3, timeout=1500):
    for i in range(attempts):
        try:
            return locator.inner_text(timeout=timeout).strip()
        except PWTimeoutError:
            if i == attempts - 1:
                return ""
            locator.page.wait_for_timeout(SETTLE_MS)


def _advance_to_new_question(frame, page, previous_title_id):
    """Keep clicking through any interstitial ad / stale-state "Next"
    buttons until a radiogroup whose question id differs from
    `previous_title_id` becomes visible. Returns (radiogroup_locator,
    title_id) on success, or (None, None) if we give up."""
    for _ in range(MAX_ADVANCE_ATTEMPTS):
        radiogroup = frame.locator('[role="radiogroup"]').first
        try:
            radiogroup.wait_for(state="visible", timeout=2500)
            title_id = _get_attribute_retry(radiogroup, "aria-labelledby")
        except PWTimeoutError:
            title_id = None

        if title_id and title_id != previous_title_id:
            return radiogroup, title_id

        # Either no radiogroup at all (an ad block, or a "TextEntry"
        # free-text question we can't meaningfully answer) or it's the same
        # stale one as before. Try clicking whatever *enabled* Next-like
        # button exists; if instead there's a text input blocking the way
        # (TextEntry blocks), submit a dummy value just to get past it —
        # we can't verify that question's answer this way, but this stops
        # it from blocking every *other* question later in the same quiz.
        next_btn = frame.locator('[data-test="next-btn"]:enabled')
        if next_btn.count() > 0:
            try:
                next_btn.first.click(timeout=1500)
            except Exception:
                pass
        else:
            textbox = frame.locator('input[type="text"], textarea, [role="textbox"]').first
            try:
                if textbox.is_visible(timeout=1000):
                    textbox.fill("x", timeout=1500)
                    submit_btn = frame.locator("button:enabled:visible").first
                    submit_btn.click(timeout=1500)
            except Exception:
                pass
        page.wait_for_timeout(SETTLE_MS)

    return None, None


def _current_image_url(frame):
    """Best-effort read of the header image shown alongside the current
    question (each question in a Riddle quiz can have its own distinct
    photo, served from cdn.riddle.com with no auth/referrer restrictions —
    so the URL can be hotlinked directly). Returns None if this question has
    no image block.

    The <img> tag is present and "visible" as soon as the question mounts,
    but its `src` is only populated a beat later (Riddle fades it in), so a
    single immediate read often sees `src=None` — retry a couple of times.
    Note this needs to retry on an empty *result*, not just a Playwright
    timeout/exception, since a successful read of a not-yet-set attribute
    just quietly returns None rather than raising."""
    img = frame.locator(".image-embed img").first
    try:
        if not img.is_visible(timeout=500):
            return None
    except PWTimeoutError:
        return None
    for i in range(4):
        try:
            src = img.get_attribute("src", timeout=800)
        except PWTimeoutError:
            src = None
        if src:
            return src
        img.page.wait_for_timeout(300)
    return None


def verify_riddle_answers(page: Page, riddle_id: str) -> dict:
    """Play through a Riddle quiz and return
    {question_text: {"answer": correct_answer_text, "image_url": str|None}}
    for every question encountered. Returns an empty (or partial) dict if the
    quiz couldn't be fully played (e.g. unsupported block types, network
    hiccups) — callers should treat this as best-effort."""
    results = {}
    previous_title_id = None
    try:
        page.goto(RIDDLE_VIEW_URL.format(riddle_id=riddle_id), wait_until="domcontentloaded", timeout=20000)
        # There can be a second, src-less top-level iframe injected alongside
        # the real quiz iframe, so target the quiz one explicitly by its src.
        frame = page.frame_locator('iframe[src*="/embed/a/"]').first

        for _ in range(MAX_QUESTIONS_SAFETY):
            radiogroup, title_id = _advance_to_new_question(frame, page, previous_title_id)
            if radiogroup is None:
                break  # quiz ended (or an unsupported/stuck state)

            radios = radiogroup.locator('[role="radio"]')
            if radios.count() == 0:
                break

            question_text = ""
            if title_id:
                question_text = _inner_text_retry(frame.locator(f"#{title_id}").first)

            image_url = _current_image_url(frame)

            # Answering with ANY option reveals the correct/incorrect tag on
            # every option, so it doesn't matter which one we click.
            radios.first.click()

            # Riddle uses a couple of different UI skins: sometimes the
            # correct/wrong marker is an <img alt="Correct answer">, other
            # times it's an <svg aria-label="Correct answer">. Matching on
            # the aria-label attribute (rather than a specific tag) covers
            # both, since aria-label works the same regardless of element.
            correct_marker = radiogroup.locator('[aria-label="Correct answer"]').first
            try:
                correct_marker.wait_for(state="visible", timeout=4000)
            except PWTimeoutError:
                # Some block types (e.g. free-text entry) may not reveal this
                # way; skip recording an answer for this one and try to move on.
                pass
            else:
                correct_radio = correct_marker.locator('xpath=ancestor::*[@role="radio"]').first
                correct_text = _inner_text_retry(correct_radio.locator("h3").first)
                if question_text and correct_text:
                    results[question_text] = {"answer": correct_text, "image_url": image_url}

            previous_title_id = title_id

            next_btn = frame.locator('[data-test="next-btn"]')
            if next_btn.count() == 0:
                break
            try:
                next_btn.first.click(timeout=3000)
            except PWTimeoutError:
                break
    except Exception:
        pass

    return results


def capture_riddle_images(page: Page, riddle_id: str) -> dict:
    """Lighter-weight sibling of verify_riddle_answers: plays through a quiz
    purely to collect {question_text: image_url}, without waiting on the
    "Correct answer" reveal (up to 4s/question saved). Used to backfill
    images onto cache entries whose answers are already verified — a "Next"
    click still needs *some* answer selected first, but we don't care which,
    or whether it's right."""
    results = {}
    previous_title_id = None
    try:
        page.goto(RIDDLE_VIEW_URL.format(riddle_id=riddle_id), wait_until="domcontentloaded", timeout=20000)
        frame = page.frame_locator('iframe[src*="/embed/a/"]').first

        for _ in range(MAX_QUESTIONS_SAFETY):
            radiogroup, title_id = _advance_to_new_question(frame, page, previous_title_id)
            if radiogroup is None:
                break

            radios = radiogroup.locator('[role="radio"]')
            if radios.count() == 0:
                break

            question_text = ""
            if title_id:
                question_text = _inner_text_retry(frame.locator(f"#{title_id}").first)

            image_url = _current_image_url(frame)
            if question_text and image_url:
                results[question_text] = image_url

            radios.first.click()
            previous_title_id = title_id

            # Skipping the "Correct answer" reveal-wait (unlike
            # verify_riddle_answers) means we also skip the settle time it
            # incidentally provided for the reveal/Next-button transition to
            # finish — wait for Next to actually appear instead of racing it.
            next_btn = frame.locator('[data-test="next-btn"]')
            try:
                next_btn.first.wait_for(state="visible", timeout=4000)
            except PWTimeoutError:
                break
            try:
                next_btn.first.click(timeout=3000)
            except PWTimeoutError:
                break
    except Exception:
        pass

    return results
