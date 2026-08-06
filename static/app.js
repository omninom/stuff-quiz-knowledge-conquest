const setupScreen = document.getElementById("setup-screen");
const loadingScreen = document.getElementById("loading-screen");
const quizScreen = document.getElementById("quiz-screen");
const resultsScreen = document.getElementById("results-screen");
const errorBox = document.getElementById("error-box");

const setupForm = document.getElementById("setup-form");
const startDateInput = document.getElementById("start_date");
const endDateInput = document.getElementById("end_date");

const progressLabel = document.getElementById("progress-label");
const progressLabelSr = document.getElementById("progress-label-sr");
const scoreValue = document.getElementById("score-value");
const questionImage = document.getElementById("question-image");
const quizMeta = document.getElementById("quiz-meta");
const questionText = document.getElementById("question-text");
const optionsList = document.getElementById("options-list");
const nextBtn = document.getElementById("next-btn");

// Exact icon paths pulled from riddle.com's own quiz player, used for the
// correct/wrong/neutral answer badges so the reveal state matches 1:1.
const CHECK_ICON_PATH =
  "M0.707108 5.12132C0.316584 5.51184 0.316584 6.14501 0.707108 6.53553L3.53554 9.36396C3.92606 9.75449 4.55922 9.75449 4.94975 9.36396L10.6066 3.70711C10.9971 3.31658 10.9971 2.68342 10.6066 2.29289C10.2161 1.90237 9.58291 1.90237 9.19239 2.29289L4.24264 7.24264L2.12132 5.12132C1.7308 4.7308 1.09763 4.7308 0.707108 5.12132Z";
const X_ICON_PATH =
  "M9.79289 1.29289C10.1834 0.902369 10.8166 0.902369 11.2071 1.29289C11.5976 1.68342 11.5976 2.31658 11.2071 2.70711L7.66421 6.25L11.2071 9.79289C11.5976 10.1834 11.5976 10.8166 11.2071 11.2071C10.8166 11.5976 10.1834 11.5976 9.79289 11.2071L6.25 7.66421L2.70711 11.2071C2.31658 11.5976 1.68342 11.5976 1.29289 11.2071C0.902369 10.8166 0.902369 10.1834 1.29289 9.79289L4.83579 6.25L1.29289 2.70711C0.902369 2.31658 0.902369 1.68342 1.29289 1.29289C1.68342 0.902369 2.31658 0.902369 2.70711 1.29289L6.25 4.83579L9.79289 1.29289Z";

function makeBadgeIcon(pathData) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 12 12");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", pathData);
  svg.appendChild(path);
  return svg;
}

// Some of the source photos are several MB (they're straight from Riddle's
// CDN, unresized), so fetching one right when a question renders was
// visibly laggy — the question/options would appear before its image did.
// Instead we kick off each question's image fetch a step ahead (as soon as
// the *previous* question renders) and cache the resulting promise, so by
// the time "Next" is clicked the image is normally already sitting in the
// browser's HTTP cache and paints instantly.
const imagePreloads = new Map(); // url -> Promise<boolean> (true = loaded ok)
const IMAGE_PRELOAD_TIMEOUT_MS = 2500;

function preloadImage(url) {
  if (!url) return Promise.resolve(false);
  if (imagePreloads.has(url)) return imagePreloads.get(url);
  const promise = new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(true);
    img.onerror = () => resolve(false);
    img.src = url;
  });
  imagePreloads.set(url, promise);
  return promise;
}

function withTimeout(promise, ms) {
  return Promise.race([promise, new Promise((resolve) => setTimeout(() => resolve(false), ms))]);
}

const resultsHeading = document.getElementById("results-heading");
const resultsList = document.getElementById("results-list");
const restartBtn = document.getElementById("restart-btn");

let questions = [];
let currentIndex = 0;
let score = 0;
let answers = []; // {question, options, correct_answer, chosen, source}

function fmtDate(d) {
  return d.toISOString().slice(0, 10);
}

// Default the date range to the last 30 days.
(function setDefaultDates() {
  const today = new Date();
  const monthAgo = new Date();
  monthAgo.setDate(today.getDate() - 30);
  endDateInput.value = fmtDate(today);
  startDateInput.value = fmtDate(monthAgo);
})();

function showScreen(screen) {
  [setupScreen, loadingScreen, quizScreen, resultsScreen].forEach((s) => s.classList.add("hidden"));
  screen.classList.remove("hidden");
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
}

function clearError() {
  errorBox.classList.add("hidden");
  errorBox.textContent = "";
}

setupForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  clearError();

  const seriesChecks = Array.from(document.querySelectorAll('input[name="series"]:checked')).map((c) => c.value);
  if (seriesChecks.length === 0) {
    showError("Select at least one quiz type.");
    return;
  }

  const payload = {
    start_date: startDateInput.value,
    end_date: endDateInput.value,
    num_questions: parseInt(document.getElementById("num_questions").value, 10) || 10,
    series: seriesChecks,
  };

  showScreen(loadingScreen);

  try {
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    if (!res.ok) {
      showScreen(setupScreen);
      showError(data.error || "Something went wrong generating the quiz.");
      return;
    }

    questions = data.questions;
    currentIndex = 0;
    score = 0;
    answers = [];

    if (questions.length < payload.num_questions) {
      // Non-fatal heads-up shown once quiz starts, via the meta line.
      console.info(
        `Only ${questions.length} of ${payload.num_questions} requested questions were available.`
      );
    }

    renderQuestion();
    showScreen(quizScreen);
  } catch (err) {
    showScreen(setupScreen);
    showError("Network error — could not reach the quiz server.");
  }
});

function renderQuestion() {
  const q = questions[currentIndex];
  progressLabel.textContent = `${currentIndex + 1} / ${questions.length}`;
  progressLabelSr.textContent = `Question ${currentIndex + 1} of ${questions.length}`;
  scoreValue.textContent = String(score);

  // Same header photo Riddle itself shows for this question (sourced
  // straight from cdn.riddle.com); falls back to a plain gradient banner
  // via CSS when a question doesn't have one cached yet. By the time we
  // get here it's normally already preloaded (see preloadImage calls
  // below/in the Next handler), so this "fadeStart"->"fadeIn" transition —
  // the same opacity 0.5s ease-out riddle.com itself uses — plays out
  // almost instantly instead of trailing behind a live network fetch.
  questionImage.classList.remove("fade-in");
  if (q.image_url) {
    questionImage.classList.remove("hidden");
    questionImage.src = q.image_url;
    const revealIfCurrent = () => {
      if (questions[currentIndex] === q) questionImage.classList.add("fade-in");
    };
    if (questionImage.complete && questionImage.naturalWidth > 0) {
      requestAnimationFrame(revealIfCurrent);
    } else {
      questionImage.onload = revealIfCurrent;
      questionImage.onerror = () => questionImage.classList.add("hidden");
    }
  } else {
    questionImage.removeAttribute("src");
    questionImage.classList.add("hidden");
  }

  // Get a head start on the *next* question's image so it's ready well
  // before the user clicks "Next" (see withTimeout usage there too).
  const upcoming = questions[currentIndex + 1];
  if (upcoming && upcoming.image_url) preloadImage(upcoming.image_url);

  quizMeta.textContent = `${q.source.series} · ${(q.source.date || "").slice(0, 10)}`;
  questionText.textContent = q.question;

  optionsList.innerHTML = "";
  nextBtn.classList.add("hidden");

  q.options.forEach((opt) => {
    const btn = document.createElement("button");
    btn.className = "riddle-choice";

    const label = document.createElement("span");
    label.className = "riddle-choice-label";
    label.textContent = opt;
    btn.appendChild(label);

    const badge = document.createElement("span");
    badge.className = "riddle-choice-badge";
    btn.appendChild(badge);

    btn.addEventListener("click", () => selectAnswer(btn, opt));
    optionsList.appendChild(btn);
  });
}

function selectAnswer(button, chosen) {
  const q = questions[currentIndex];
  const isCorrect = chosen === q.correct_answer;

  Array.from(optionsList.children).forEach((btn) => {
    btn.disabled = true;
    const badge = btn.querySelector(".riddle-choice-badge");
    const label = btn.querySelector(".riddle-choice-label").textContent;

    if (label === q.correct_answer) {
      btn.classList.add("is-correct");
      badge.appendChild(makeBadgeIcon(CHECK_ICON_PATH));
    } else if (btn === button) {
      btn.classList.add("is-wrong-selected");
      badge.appendChild(makeBadgeIcon(X_ICON_PATH));
    } else {
      btn.classList.add("is-neutral");
      badge.appendChild(makeBadgeIcon(X_ICON_PATH));
    }
  });

  if (isCorrect) {
    score += 1;
    scoreValue.textContent = String(score);
  }
  answers.push({
    question: q.question,
    options: q.options,
    correct_answer: q.correct_answer,
    chosen,
    isCorrect,
    source: q.source,
  });

  nextBtn.textContent = currentIndex === questions.length - 1 ? "See results" : "Next";
  nextBtn.classList.remove("hidden");
}

nextBtn.addEventListener("click", async () => {
  const upcoming = questions[currentIndex + 1];
  if (upcoming && upcoming.image_url) {
    // Almost always already resolved (preloaded while the current question
    // was on screen) — this just guards the rare case someone answers
    // faster than the image download, capped so a slow/broken image can
    // never block the quiz from advancing.
    nextBtn.disabled = true;
    await withTimeout(preloadImage(upcoming.image_url), IMAGE_PRELOAD_TIMEOUT_MS);
    nextBtn.disabled = false;
  }

  currentIndex += 1;
  if (currentIndex >= questions.length) {
    showResults();
  } else {
    renderQuestion();
  }
});

function showResults() {
  resultsHeading.textContent = `You scored ${score} / ${questions.length}`;
  resultsList.innerHTML = "";

  answers.forEach((a) => {
    const item = document.createElement("div");
    item.className = `result-item ${a.isCorrect ? "correct" : "incorrect"}`;

    const q = document.createElement("div");
    q.className = "result-question";
    q.textContent = a.question;
    item.appendChild(q);

    const ans = document.createElement("div");
    ans.className = "result-answer";
    ans.innerHTML = a.isCorrect
      ? `You answered <strong>${a.chosen}</strong> — correct!`
      : `You answered <strong>${a.chosen}</strong>. Correct answer: <strong>${a.correct_answer}</strong>`;
    item.appendChild(ans);

    const src = document.createElement("div");
    src.className = "result-source";
    src.innerHTML = `From: <a href="${a.source.url}" target="_blank" rel="noopener">${a.source.series} — ${(a.source.date || "").slice(0, 10)}</a>`;
    item.appendChild(src);

    resultsList.appendChild(item);
  });

  showScreen(resultsScreen);
}

restartBtn.addEventListener("click", () => {
  showScreen(setupScreen);
});
