// Point this at wherever you deploy the FastAPI backend (e.g. Render).
const API_BASE_URL = "http://localhost:8000";

const els = {
  tabs: document.querySelectorAll(".tab"),
  panels: {
    pdf: document.getElementById("panel-pdf"),
    youtube: document.getElementById("panel-youtube"),
  },
  dropzone: document.getElementById("dropzone"),
  dzTitle: document.getElementById("dz-title"),
  pdfInput: document.getElementById("pdf-input"),
  pdfStartBtn: document.getElementById("pdf-start-btn"),
  ytInput: document.getElementById("yt-input"),
  ytSubmit: document.getElementById("yt-submit"),
  wordsRange: document.getElementById("words-per-chunk"),
  wordsVal: document.getElementById("words-per-chunk-val"),
  strategySelect: document.getElementById("strategy-select"),
  status: document.getElementById("status"),
  progressTrack: document.getElementById("progress-track"),
  progressFill: document.getElementById("progress-fill"),
  deckSection: document.getElementById("deck-section"),
  deckTitle: document.getElementById("deck-title"),
  deckMeta: document.getElementById("deck-meta"),
  deck: document.getElementById("deck"),
  studyBtn: document.getElementById("study-btn"),
  streakBadge: document.getElementById("streak-badge"),
  streakCount: document.getElementById("streak-count"),
  usageBadge: document.getElementById("usage-badge"),
  studyOverlay: document.getElementById("study-overlay"),
  studyExit: document.getElementById("study-exit"),
  studyProgressFill: document.getElementById("study-progress-fill"),
  studyCount: document.getElementById("study-count"),
  studyCard: document.getElementById("study-card"),
  studyCardIndex: document.getElementById("study-card-index"),
  studyCardText: document.getElementById("study-card-text"),
  studyBtnReview: document.getElementById("study-btn-review"),
  studyBtnKnown: document.getElementById("study-btn-known"),
  studyDone: document.getElementById("study-done"),
  studyRestart: document.getElementById("study-restart"),
  studyActions: document.querySelector(".study-actions"),
  studyStage: document.querySelector(".study-stage"),
};

let activeTab = "pdf";
let currentDeck = null;
let isProcessing = false;
let selectedPdfFile = null;
let pollTimer = null;

// setProcessing locks tab switching + the slider + both intake controls for
// the whole duration of a job - this is what fixes "switching tabs mid-job
// looked like it reset everything." Disabled buttons don't fire click
// events at all, so this isn't just a visual state.
function setProcessing(state) {
  isProcessing = state;
  els.tabs.forEach(t => (t.disabled = state));
  els.dropzone.classList.toggle("locked", state);
  els.pdfInput.disabled = state;
  els.pdfStartBtn.disabled = state;
  els.ytInput.disabled = state;
  els.ytSubmit.disabled = state;
  els.wordsRange.disabled = state;
}

// ---------- Tabs ----------
els.tabs.forEach(tab => {
  tab.addEventListener("click", () => {
    activeTab = tab.dataset.tab;
    els.tabs.forEach(t => t.classList.toggle("active", t === tab));
    Object.entries(els.panels).forEach(([key, panel]) =>
      panel.classList.toggle("active", key === activeTab)
    );
    if (activeTab === "youtube") {
      els.strategySelect.value = "fixed";
      els.strategySelect.disabled = true;
    } else {
      els.strategySelect.disabled = false;
    }
    setStatus("");
    hideProgress();
  });
});

els.wordsRange.addEventListener("input", () => {
  els.wordsVal.textContent = els.wordsRange.value;
});

function setStatus(message, kind) {
  els.status.textContent = message;
  els.status.className = "status" + (kind ? " " + kind : "");
}

function showProgress(percent, label) {
  els.progressTrack.hidden = false;
  els.progressFill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
  if (label) setStatus(label, "busy");
}

function hideProgress() {
  els.progressTrack.hidden = true;
  els.progressFill.style.width = "0%";
}

// ---------------------------------------------------------------------------
// Streak - saved in THIS BROWSER's localStorage only, under
// "microchunk_streak" / "microchunk_last_active". Not sent to the backend,
// not tied to an account - a different browser/device or clearing site
// data starts it fresh. Only credits real chunk jobs, not page visits, and
// shows a broken streak immediately if a full day was skipped.
// ---------------------------------------------------------------------------
function daysBetween(a, b) {
  const dayMs = 24 * 60 * 60 * 1000;
  return Math.round((new Date(b) - new Date(a)) / dayMs);
}

function renderStreak(streak) {
  els.streakCount.textContent = streak;
  els.streakBadge.classList.toggle("lit", streak >= 1);
}

function loadStreakDisplay() {
  const today = new Date().toISOString().slice(0, 10);
  const lastActive = localStorage.getItem("microchunk_last_active");
  let streak = parseInt(localStorage.getItem("microchunk_streak") || "0", 10);
  if (lastActive && daysBetween(lastActive, today) > 1) {
    streak = 0;
    localStorage.setItem("microchunk_streak", "0");
  }
  renderStreak(streak);
}

function recordStreakActivity() {
  const today = new Date().toISOString().slice(0, 10);
  const lastActive = localStorage.getItem("microchunk_last_active");
  let streak = parseInt(localStorage.getItem("microchunk_streak") || "0", 10);
  if (lastActive === today) {
    // already credited today
  } else if (lastActive && daysBetween(lastActive, today) === 1) {
    streak += 1;
  } else {
    streak = 1;
  }
  localStorage.setItem("microchunk_last_active", today);
  localStorage.setItem("microchunk_streak", String(streak));
  renderStreak(streak);
}

// ---------- Usage badge (now MB-based, not a job count) ----------
const DEFAULT_DAILY_MB_LIMIT = 40; // must match the backend's DAILY_MB_LIMIT default

function applyUsage(usage) {
  if (!usage) return;
  els.usageBadge.textContent = `${usage.remaining_mb}MB left today`;
  els.usageBadge.classList.toggle("low", usage.remaining_mb <= 2);
}

async function refreshUsageBadge() {
  applyUsage({ remaining_mb: DEFAULT_DAILY_MB_LIMIT, limit_mb: DEFAULT_DAILY_MB_LIMIT });
  try {
    const res = await fetch(`${API_BASE_URL}/api/usage`);
    const data = await res.json();
    applyUsage(data);
  } catch {
    // backend unreachable - keep showing the assumed default above
  }
}

// ---------------------------------------------------------------------------
// PDF intake: selecting a file no longer starts anything by itself - it
// just shows a "Start chunking" button. Processing (and the tab/slider
// lock) only begins once that button is actually pressed.
// ---------------------------------------------------------------------------
els.dropzone.addEventListener("dragover", e => {
  e.preventDefault();
  els.dropzone.classList.add("drag");
});
els.dropzone.addEventListener("dragleave", () => els.dropzone.classList.remove("drag"));
els.dropzone.addEventListener("drop", e => {
  e.preventDefault();
  els.dropzone.classList.remove("drag");
  const file = e.dataTransfer.files[0];
  if (file) selectPdfFile(file);
});
els.pdfInput.addEventListener("change", () => {
  const file = els.pdfInput.files[0];
  if (file) selectPdfFile(file);
});

function selectPdfFile(file) {
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    setStatus("That doesn't look like a PDF.", "error");
    return;
  }
  selectedPdfFile = file;
  els.dzTitle.textContent = `${file.name} — ready`;
  els.pdfStartBtn.hidden = false;
  setStatus("");
}

els.pdfStartBtn.addEventListener("click", () => {
  if (selectedPdfFile) startPdfJob(selectedPdfFile);
});

async function startPdfJob(file) {
  const strategy = els.strategySelect.value;
  setProcessing(true);
  showProgress(0, strategy === "semantic" ? "Starting (semantic mode is slower) …" : "Starting …");

  const formData = new FormData();
  formData.append("file", file);

  try {
    const wordsPerChunk = els.wordsRange.value;
    const startRes = await fetch(
      `${API_BASE_URL}/api/pdf/start?words_per_chunk=${wordsPerChunk}&strategy=${strategy}`,
      { method: "POST", body: formData }
    );
    const startData = await startRes.json();
    if (!startRes.ok) throw new Error(startData.detail || "Something went wrong.");

    applyUsage(startData.usage);
    await pollPdfJob(startData.job_id, file, strategy);
  } catch (err) {
    setStatus(err.message, "error");
    hideProgress();
    setProcessing(false);
    if (err.message && err.message.toLowerCase().includes("daily free limit")) {
      refreshUsageBadge();
    }
  }
}

function pollPdfJob(jobId, file, strategy) {
  return new Promise((resolve, reject) => {
    pollTimer = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/api/pdf/progress/${jobId}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Lost track of that job.");

        if (data.status === "processing") {
          const pageNote = data.total_pages ? ` (page ${Math.ceil((data.percent / 100) * data.total_pages)}/${data.total_pages})` : "";
          showProgress(data.percent, `${data.phase}${pageNote} …`);
        } else if (data.status === "done") {
          clearInterval(pollTimer);
          showProgress(100, "Done!");
          recordStreakActivity();
          renderDeck(data.result, {
            title: file.name.replace(/\.pdf$/i, ""),
            meta: `${data.result.page_count} pages · ${data.result.total_words.toLocaleString()} words · ${data.result.chunk_count} cards · ${data.result.strategy} chunking`,
          });
          setStatus(`Done — ${data.result.chunk_count} cards ready below.`);
          setTimeout(hideProgress, 800);
          setProcessing(false);
          selectedPdfFile = null;
          els.pdfStartBtn.hidden = true;
          resolve();
        } else if (data.status === "error") {
          clearInterval(pollTimer);
          throw new Error(data.detail || "Couldn't process that PDF.");
        }
      } catch (err) {
        clearInterval(pollTimer);
        setStatus(err.message, "error");
        hideProgress();
        setProcessing(false);
        reject(err);
      }
    }, 350);
  });
}

// ---------- YouTube intake ----------
els.ytSubmit.addEventListener("click", handleYoutube);
els.ytInput.addEventListener("keydown", e => {
  if (e.key === "Enter") handleYoutube();
});

async function handleYoutube() {
  const url = els.ytInput.value.trim();
  if (!url) {
    setStatus("Paste a YouTube URL first.", "error");
    return;
  }
  setStatus("Fetching captions and chunking …", "busy");
  setProcessing(true);

  try {
    const wordsPerChunk = Number(els.wordsRange.value);
    const res = await fetch(`${API_BASE_URL}/api/youtube`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, words_per_chunk: wordsPerChunk, strategy: "fixed" }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Something went wrong.");

    applyUsage(data.usage);
    recordStreakActivity();
    renderDeck(data, {
      title: `youtu.be/${data.video_id}`,
      meta: `${data.total_words.toLocaleString()} words · ${data.chunk_count} cards`,
    });
    setStatus(`Done — ${data.chunk_count} cards ready below.`);
  } catch (err) {
    setStatus(err.message, "error");
    if (err.message && err.message.toLowerCase().includes("daily free limit")) {
      refreshUsageBadge();
    }
  } finally {
    setProcessing(false);
  }
}

// ---------- Rendering ----------
function renderDeck(data, { title, meta }) {
  els.deckTitle.textContent = title;
  els.deckMeta.textContent = meta;
  els.deck.innerHTML = "";
  currentDeck = data;

  data.chunks.forEach(chunk => {
    const card = document.createElement("article");
    card.className = "card";
    card.style.animationDelay = `${Math.min(chunk.index * 18, 300)}ms`;

    const idx = document.createElement("div");
    idx.className = "card-index";
    idx.textContent = `No. ${String(chunk.index + 1).padStart(2, "0")}`;
    card.appendChild(idx);

    const text = document.createElement("div");
    text.className = "card-text";
    text.textContent = chunk.text;
    card.appendChild(text);

    const foot = document.createElement("div");
    foot.className = "card-foot";

    const wc = document.createElement("span");
    wc.textContent = `${chunk.word_count} words`;
    foot.appendChild(wc);

    if (data.source === "youtube") {
      const link = document.createElement("a");
      link.href = `https://youtu.be/${data.video_id}?t=${Math.floor(chunk.start_time)}`;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = formatTimestamp(chunk.start_time);
      foot.appendChild(link);
    }

    card.appendChild(foot);
    els.deck.appendChild(card);
  });

  els.deckSection.hidden = false;
  els.deckSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function formatTimestamp(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

// ---------------------------------------------------------------------------
// Study Mode: active-recall flashcard review. Cards marked "Review again"
// go to the back of the queue instead of disappearing.
// ---------------------------------------------------------------------------
let studyQueue = [];
let studyTotal = 0;

els.studyBtn.addEventListener("click", () => {
  if (!currentDeck || !currentDeck.chunks || currentDeck.chunks.length === 0) return;
  studyQueue = currentDeck.chunks.map(c => ({ index: c.index, text: c.text }));
  studyTotal = studyQueue.length;
  els.studyOverlay.hidden = false;
  els.studyDone.hidden = true;
  els.studyStage.hidden = false;
  els.studyActions.hidden = false;
  showNextStudyCard();
});

els.studyExit.addEventListener("click", () => { els.studyOverlay.hidden = true; });
els.studyRestart.addEventListener("click", () => els.studyBtn.click());

function showNextStudyCard() {
  if (studyQueue.length === 0) {
    els.studyStage.hidden = true;
    els.studyActions.hidden = true;
    els.studyDone.hidden = false;
    return;
  }
  const card = studyQueue[0];
  els.studyCardIndex.textContent = `No. ${String(card.index + 1).padStart(2, "0")}`;
  els.studyCardText.textContent = card.text;

  const doneSoFar = studyTotal - studyQueue.length;
  els.studyCount.textContent = `${doneSoFar} / ${studyTotal}`;
  els.studyProgressFill.style.width = `${(doneSoFar / studyTotal) * 100}%`;
}

els.studyBtnKnown.addEventListener("click", () => {
  studyQueue.shift();
  showNextStudyCard();
});

els.studyBtnReview.addEventListener("click", () => {
  const card = studyQueue.shift();
  studyQueue.push(card);
  showNextStudyCard();
});

// ---------- Init ----------
loadStreakDisplay();
refreshUsageBadge();
