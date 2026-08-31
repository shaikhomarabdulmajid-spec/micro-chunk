// Point this at wherever you deploy the FastAPI backend (e.g. Render).
const API_BASE_URL = "http://localhost:8000";

const els = {
  dropzone: document.getElementById("dropzone"),
  dzTitle: document.getElementById("dz-title"),
  pdfInput: document.getElementById("pdf-input"),
  pdfStartBtn: document.getElementById("pdf-start-chunking-btn"),
  wordsRange: document.getElementById("words-per-chunk"),
  wordsVal: document.getElementById("words-per-chunk-val"),
  strategySelect: document.getElementById("strategy-select"),
  semanticOption: document.getElementById("semantic-option"),
  status: document.getElementById("status"),
  progressTrack: document.getElementById("progress-track"),
  progressFill: document.getElementById("progress-fill"),
  deckSection: document.getElementById("deck-section"),
  deckTitle: document.getElementById("deck-title"),
  deckMeta: document.getElementById("deck-meta"),
  deck: document.getElementById("deck"),
  topicNavList: document.getElementById("topic-nav-list"),
  yourDecksSection: document.getElementById("your-decks"),
  deckList: document.getElementById("deck-list"),
  studyBtn: document.getElementById("study-btn"),
  streakBadge: document.getElementById("streak-badge"),
  streakCount: document.getElementById("streak-count"),
  usageBadge: document.getElementById("usage-badge"),
  studyOverlay: document.getElementById("study-overlay"),
  studyExit: document.getElementById("study-exit"),
  notGottenTab: document.getElementById("study-not-gotten-tab"),
  notGottenCount: document.getElementById("not-gotten-count"),
  notGottenPanel: document.getElementById("study-not-gotten-panel"),
  notGottenList: document.getElementById("not-gotten-list"),
  notGottenBack: document.getElementById("not-gotten-back"),
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

let currentDeck = null;
let isProcessing = false;
let selectedPdfFile = null;

// setProcessing locks the dropzone + slider + strategy select for the
// whole duration of a job - disabled controls don't fire click events at
// all, so this isn't just a visual state.
function setProcessing(state) {
  isProcessing = state;
  els.dropzone.classList.toggle("locked", state);
  els.pdfInput.disabled = state;
  els.pdfStartBtn.disabled = state;
  els.wordsRange.disabled = state;
  els.strategySelect.disabled = state;
}

els.wordsRange.addEventListener("input", () => {
  els.wordsVal.textContent = els.wordsRange.value;
});

function setStatus(message, kind) {
  els.status.textContent = message;
  els.status.className = "status" + (kind ? " " + kind : "");
}

// Indeterminate progress: an animated bar that communicates "working"
// without inventing a percentage that doesn't correspond to anything real.
// Paired with an honest elapsed-time counter (actual seconds elapsed, not
// a guess) so there's still a sense of progress without a fake number.
let elapsedTimer = null;

function showProgress(label) {
  els.progressTrack.hidden = false;
  els.progressTrack.classList.add("indeterminate");
  const startedAt = Date.now();
  setStatus(`${label} (0s)`, "busy");
  elapsedTimer = setInterval(() => {
    const secs = Math.floor((Date.now() - startedAt) / 1000);
    setStatus(`${label} (${secs}s)`, "busy");
  }, 1000);
}

function hideProgress() {
  clearInterval(elapsedTimer);
  els.progressTrack.hidden = true;
  els.progressTrack.classList.remove("indeterminate");
}

// ---------------------------------------------------------------------------
// Streak - saved in THIS BROWSER's localStorage only, under
// "microchunk_streak" / "microchunk_last_active". Not sent to the backend,
// not tied to an account - a different browser/device or clearing site
// data starts it fresh.
//
// Purely visit-based: the very first time you ever open the app, the
// streak shows 0. The NEXT calendar day you open it, it becomes 1. The
// day after that, 2, and so on. If you skip a day, it drops back to 0 on
// your next visit (treated like a fresh start, not an instant 1) - so the
// badge always means "consecutive days in a row," never a lie.
// ---------------------------------------------------------------------------
function daysBetween(a, b) {
  const dayMs = 24 * 60 * 60 * 1000;
  return Math.round((new Date(b) - new Date(a)) / dayMs);
}

function renderStreak(streak, { justUpdated = false } = {}) {
  els.streakCount.textContent = streak;
  els.streakBadge.classList.toggle("lit", streak >= 1);
  if (justUpdated) {
    els.streakBadge.classList.remove("pulse");
    void els.streakBadge.offsetWidth; // reflow so the animation can retrigger
    els.streakBadge.classList.add("pulse");
  }
}

function updateStreakForVisit() {
  const today = new Date().toISOString().slice(0, 10);
  const lastVisit = localStorage.getItem("microchunk_last_active");
  let streak = parseInt(localStorage.getItem("microchunk_streak") || "0", 10);

  if (!lastVisit) {
    streak = 0; // very first visit ever
  } else if (lastVisit === today) {
    // already counted today, no change
  } else if (daysBetween(lastVisit, today) === 1) {
    streak += 1; // came back exactly the next day
  } else {
    streak = 0; // missed a day or more - starts over, same as a first visit
  }

  localStorage.setItem("microchunk_last_active", today);
  localStorage.setItem("microchunk_streak", String(streak));
  renderStreak(streak, { justUpdated: true });
}

// ---------- Usage badge (MB-based, not a job count) ----------
const DEFAULT_DAILY_MB_LIMIT = 40; // must match the backend's DAILY_MB_LIMIT default
const DEFAULT_SEMANTIC_LIMIT = 1; // must match the backend's SEMANTIC_DAILY_LIMIT default

function applyUsage(usage) {
  if (!usage) return;
  els.usageBadge.textContent = `${usage.remaining_mb}MB left today`;
  els.usageBadge.classList.toggle("low", usage.remaining_mb <= 2);

  if (usage.semantic_remaining !== undefined) {
    if (usage.semantic_remaining > 0) {
      els.semanticOption.textContent = `Semantic (topic-aware, PDF only) — ${usage.semantic_remaining} left today`;
      els.semanticOption.disabled = false;
    } else {
      els.semanticOption.textContent = "Semantic — used today, back tomorrow";
      els.semanticOption.disabled = true;
      if (els.strategySelect.value === "semantic") els.strategySelect.value = "fixed";
    }
  }
}

async function refreshUsageBadge() {
  applyUsage({ remaining_mb: DEFAULT_DAILY_MB_LIMIT, limit_mb: DEFAULT_DAILY_MB_LIMIT, semantic_remaining: DEFAULT_SEMANTIC_LIMIT });
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
  showProgress(strategy === "semantic" ? "Grouping by topic (semantic mode is slower)" : "Chunking");

  const formData = new FormData();
  formData.append("file", file);

  try {
    const wordsPerChunk = els.wordsRange.value;
    const res = await fetch(
      `${API_BASE_URL}/api/pdf?words_per_chunk=${wordsPerChunk}&strategy=${strategy}`,
      { method: "POST", body: formData }
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Something went wrong.");

    applyUsage(data.usage);
    const deckTitle = file.name.replace(/\.pdf$/i, "");
    const deckMeta = `${data.page_count} pages · ${data.total_words.toLocaleString()} words · ${data.chunk_count} cards · ${data.strategy} chunking`;
    const deckId = saveDeck(data, deckTitle, deckMeta);
    renderDeck(data, { title: deckTitle, meta: deckMeta, deckId });
    renderDeckList();
    setStatus(`Done — ${data.chunk_count} cards ready below.`);
    selectedPdfFile = null;
    els.pdfStartBtn.hidden = true;
  } catch (err) {
    setStatus(err.message, "error");
    const msg = (err.message || "").toLowerCase();
    if (msg.includes("daily free limit") || msg.includes("free semantic chunking")) {
      refreshUsageBadge();
    }
  } finally {
    hideProgress();
    setProcessing(false);
  }
}

// ---------- Rendering ----------
function renderDeck(data, { title, meta, deckId }) {
  els.deckTitle.textContent = title;
  els.deckMeta.textContent = meta;
  els.deck.innerHTML = "";
  els.topicNavList.innerHTML = "";
  currentDeck = { ...data, deckId };

  if (data.fallback_reason) {
    const note = document.createElement("div");
    note.className = "fallback-note";
    note.textContent = data.fallback_reason;
    els.deck.appendChild(note);
  }

  // Group consecutive cards sharing a heading into one topic - chunks
  // already arrive in document order with the same heading for everything
  // in one section, so a simple consecutive-run grouping is enough. Cards
  // with no heading (content before the first detected heading) fall
  // into a single "General" topic.
  const sections = [];
  data.chunks.forEach(chunk => {
    const heading = chunk.heading || null;
    const last = sections[sections.length - 1];
    if (last && last.heading === heading) {
      last.cards.push(chunk);
    } else {
      sections.push({ heading, cards: [chunk] });
    }
  });

  sections.forEach((section, i) => {
    const topicId = `topic-${i}`;
    const displayName = section.heading || "General";

    // Sidebar nav entry
    const navItem = document.createElement("li");
    const navLink = document.createElement("a");
    navLink.href = `#${topicId}`;
    navLink.textContent = displayName;
    navLink.dataset.topicId = topicId;
    navLink.addEventListener("click", e => {
      e.preventDefault();
      expandTopic(topicId);
      document.getElementById(topicId).scrollIntoView({ behavior: "smooth", block: "start" });
    });
    navItem.appendChild(navLink);
    els.topicNavList.appendChild(navItem);

    // Main accordion section - collapsed by default, only the heading and
    // card count show until the toggle is pressed.
    const sectionEl = document.createElement("section");
    sectionEl.className = "topic-section";
    sectionEl.id = topicId;

    const toggle = document.createElement("button");
    toggle.className = "topic-toggle";
    toggle.setAttribute("aria-expanded", "false");
    toggle.innerHTML = `
      <span class="topic-title"></span>
      <span class="topic-count"></span>
      <span class="topic-chevron">▾</span>
    `;
    toggle.querySelector(".topic-title").textContent = displayName;
    toggle.querySelector(".topic-count").textContent =
      `${section.cards.length} card${section.cards.length === 1 ? "" : "s"}`;

    const cardsRow = document.createElement("div");
    cardsRow.className = "topic-cards-row";
    cardsRow.hidden = true;

    const track = document.createElement("div");
    track.className = "topic-cards-track";

    section.cards.forEach(chunk => {
      const card = document.createElement("article");
      card.className = "card";
      card.dataset.no = String(chunk.index + 1).padStart(3, "0");
      card.style.animationDelay = `${Math.min(chunk.index * 18, 300)}ms`;

      const text = document.createElement("div");
      text.className = "card-text";
      text.textContent = chunk.text;
      card.appendChild(text);

      const foot = document.createElement("div");
      foot.className = "card-foot";

      const wc = document.createElement("span");
      wc.textContent = `${chunk.word_count} words`;
      foot.appendChild(wc);

      card.appendChild(foot);
      track.appendChild(card);
    });

    cardsRow.appendChild(track);

    toggle.addEventListener("click", () => {
      const isOpen = toggle.getAttribute("aria-expanded") === "true";
      setTopicExpanded(topicId, !isOpen);
    });

    sectionEl.appendChild(toggle);
    sectionEl.appendChild(cardsRow);
    els.deck.appendChild(sectionEl);
  });

  els.deckSection.hidden = false;
  els.deckSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function setTopicExpanded(topicId, expanded) {
  const sectionEl = document.getElementById(topicId);
  if (!sectionEl) return;
  const toggle = sectionEl.querySelector(".topic-toggle");
  const row = sectionEl.querySelector(".topic-cards-row");
  toggle.setAttribute("aria-expanded", String(expanded));
  row.hidden = !expanded;

  const navLink = els.topicNavList.querySelector(`a[data-topic-id="${topicId}"]`);
  if (navLink) navLink.classList.toggle("active", expanded);
}

function expandTopic(topicId) {
  setTopicExpanded(topicId, true);
}

// ---------------------------------------------------------------------------
// Deck persistence - saved decks live in localStorage under
// "microchunk_decks" (keyed by a generated deck id), so you can come back
// tomorrow and actually have something to review, instead of Study Mode
// only ever covering whatever you just generated in this one sitting.
// Capped at 20 saved decks (oldest dropped first) so this doesn't grow
// unbounded in the browser.
// ---------------------------------------------------------------------------
const MAX_SAVED_DECKS = 20;

function loadDecks() {
  try {
    return JSON.parse(localStorage.getItem("microchunk_decks") || "{}");
  } catch {
    return {};
  }
}

function saveDecksMap(map) {
  localStorage.setItem("microchunk_decks", JSON.stringify(map));
}

function saveDeck(data, title, meta) {
  const decks = loadDecks();
  const id = "deck_" + Date.now() + "_" + Math.random().toString(36).slice(2, 7);
  decks[id] = {
    id,
    title,
    meta,
    source: data.source,
    video_id: data.video_id || null,
    chunks: data.chunks,
    savedAt: new Date().toISOString(),
  };

  const ids = Object.keys(decks).sort((a, b) => new Date(decks[a].savedAt) - new Date(decks[b].savedAt));
  while (ids.length > MAX_SAVED_DECKS) {
    const oldestId = ids.shift();
    delete decks[oldestId];
    removeDeckSRS(oldestId);
  }

  saveDecksMap(decks);
  return id;
}

function deleteDeck(id) {
  const decks = loadDecks();
  delete decks[id];
  saveDecksMap(decks);
  removeDeckSRS(id);
}

// ---------------------------------------------------------------------------
// Spaced repetition - real SM-2 (the same scheduling algorithm Anki is
// built on), not a same-session-only shuffle. Per-card state lives in
// localStorage under "microchunk_srs", keyed by "<deckId>:<cardIndex>".
// A card with no record yet is always due (never been reviewed).
// ---------------------------------------------------------------------------
function loadSRS() {
  try {
    return JSON.parse(localStorage.getItem("microchunk_srs") || "{}");
  } catch {
    return {};
  }
}

function saveSRS(all) {
  localStorage.setItem("microchunk_srs", JSON.stringify(all));
}

function srsKey(deckId, cardIndex) {
  return `${deckId}:${cardIndex}`;
}

function removeDeckSRS(deckId) {
  const all = loadSRS();
  Object.keys(all).forEach(k => {
    if (k.startsWith(deckId + ":")) delete all[k];
  });
  saveSRS(all);
}

function getCardSRS(deckId, cardIndex) {
  return loadSRS()[srsKey(deckId, cardIndex)] || null;
}

function isCardDue(deckId, cardIndex) {
  const rec = getCardSRS(deckId, cardIndex);
  if (!rec) return true; // never reviewed - always due
  return rec.dueDate <= new Date().toISOString().slice(0, 10);
}

// quality: 4 = "Got it", 2 = "Review again" - the standard SM-2 formula,
// just fed a binary input instead of a 0-5 scale.
function scheduleNext(record, quality) {
  let { interval = 0, ease = 2.5, reps = 0 } = record || {};
  if (quality < 3) {
    reps = 0;
    interval = 1;
  } else {
    if (reps === 0) interval = 1;
    else if (reps === 1) interval = 6;
    else interval = Math.round(interval * ease);
    reps += 1;
  }
  ease = Math.max(1.3, ease + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)));
  ease = Math.round(ease * 100) / 100;

  const due = new Date();
  due.setDate(due.getDate() + interval);
  return { interval, ease, reps, dueDate: due.toISOString().slice(0, 10) };
}

function updateCardSRS(deckId, cardIndex, quality) {
  const all = loadSRS();
  const key = srsKey(deckId, cardIndex);
  all[key] = scheduleNext(all[key], quality);
  saveSRS(all);
  return all[key];
}

function countDue(deck) {
  return deck.chunks.filter(c => isCardDue(deck.id, c.index)).length;
}

// ---------------------------------------------------------------------------
// "Your Decks" list - lets you reopen a previously generated deck (no
// re-upload, no network call) so there's something to actually come back
// to for spaced repetition to mean anything.
// ---------------------------------------------------------------------------
function renderDeckList() {
  const decks = Object.values(loadDecks()).sort((a, b) => new Date(b.savedAt) - new Date(a.savedAt));
  els.yourDecksSection.hidden = decks.length === 0;
  els.deckList.innerHTML = "";

  decks.forEach(deck => {
    const due = countDue(deck);

    const item = document.createElement("div");
    item.className = "deck-list-item";

    const info = document.createElement("div");
    info.className = "deck-list-info";
    const titleEl = document.createElement("p");
    titleEl.className = "deck-list-title";
    titleEl.textContent = deck.title;
    const metaEl = document.createElement("p");
    metaEl.className = "deck-list-meta";
    metaEl.textContent = deck.meta;
    info.appendChild(titleEl);
    info.appendChild(metaEl);

    const dueBadge = document.createElement("span");
    dueBadge.className = "deck-list-due" + (due === 0 ? " none" : "");
    dueBadge.textContent = due === 0 ? "nothing due" : `${due} due`;

    const openBtn = document.createElement("button");
    openBtn.className = "deck-list-open";
    openBtn.textContent = "Open";
    openBtn.addEventListener("click", () => openSavedDeck(deck));

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "deck-list-delete";
    deleteBtn.textContent = "✕";
    deleteBtn.title = "Delete this deck";
    deleteBtn.addEventListener("click", () => {
      deleteDeck(deck.id);
      renderDeckList();
    });

    item.appendChild(info);
    item.appendChild(dueBadge);
    item.appendChild(openBtn);
    item.appendChild(deleteBtn);
    els.deckList.appendChild(item);
  });
}

function openSavedDeck(deck) {
  renderDeck(
    { source: deck.source, video_id: deck.video_id, chunks: deck.chunks },
    { title: deck.title, meta: deck.meta, deckId: deck.id }
  );
}

// ---------------------------------------------------------------------------
// Study Mode: active-recall review, now scheduled with real spaced
// repetition. Within one sitting, "Review again" still brings a card back
// later in the SAME session for immediate re-drilling (short-term
// practice) - but it also schedules that card's next real review for
// tomorrow via SM-2, and "Got it" pushes it out further (1 day, then 6,
// then increasingly longer). Only cards actually due today are queued.
// ---------------------------------------------------------------------------
let studyQueue = [];
let studyTotal = 0;
let studyDeckId = null;
let studyBusy = false; // guards against a fast double-click grading two cards at once
let notGotten = new Map(); // index -> card, cards currently graded "Review again" this session

els.studyBtn.addEventListener("click", () => {
  if (!currentDeck || !currentDeck.chunks || currentDeck.chunks.length === 0) return;

  // If you hit Exit mid-deck and come right back, pick up exactly where you
  // left off instead of silently discarding your progress and recomputing
  // a fresh due-list from scratch (which reset the counter back to 0 even
  // though you hadn't actually lost any graded cards).
  const resuming = studyDeckId === currentDeck.deckId && studyQueue.length > 0;

  if (!resuming) {
    studyDeckId = currentDeck.deckId;
    const dueCards = currentDeck.chunks.filter(c => isCardDue(studyDeckId, c.index));
    if (dueCards.length === 0) {
      setStatus("Nothing due for review in this deck today - come back tomorrow!", "");
      return;
    }
    studyQueue = dueCards.map(c => ({ index: c.index, text: c.text }));
    studyTotal = studyQueue.length;
    notGotten = new Map();
  }

  studyBusy = false;
  els.studyOverlay.hidden = false;
  els.studyDone.hidden = true;
  els.studyStage.hidden = false;
  els.studyActions.hidden = false;
  els.notGottenPanel.hidden = true;
  renderNotGottenTab();
  showNextStudyCard();
});

els.studyExit.addEventListener("click", () => {
  els.studyOverlay.hidden = true;
  renderDeckList(); // due counts may have changed
});
els.studyRestart.addEventListener("click", () => els.studyBtn.click());

function renderNotGottenTab() {
  els.notGottenCount.textContent = notGotten.size;
  els.notGottenTab.hidden = notGotten.size === 0;
}

els.notGottenTab.addEventListener("click", () => {
  els.notGottenList.innerHTML = "";
  for (const card of notGotten.values()) {
    const item = document.createElement("div");
    item.className = "not-gotten-item";
    item.innerHTML = `<span class="mono not-gotten-no">No. ${String(card.index + 1).padStart(2, "0")}</span><span>${escapeHtml(card.text)}</span>`;
    els.notGottenList.appendChild(item);
  }
  els.studyStage.hidden = true;
  els.studyActions.hidden = true;
  els.notGottenPanel.hidden = false;
});

els.notGottenBack.addEventListener("click", () => {
  els.notGottenPanel.hidden = true;
  els.studyStage.hidden = false;
  els.studyActions.hidden = false;
});

function showNextStudyCard() {
  if (studyQueue.length === 0) {
    els.studyStage.hidden = true;
    els.studyActions.hidden = true;
    els.studyDone.hidden = false;
    burstConfetti();
    renderDeckList();
    return;
  }
  const card = studyQueue[0];
  els.studyCardIndex.textContent = `No. ${String(card.index + 1).padStart(2, "0")}`;
  els.studyCardText.textContent = card.text;

  const doneSoFar = studyTotal - studyQueue.length;
  els.studyCount.textContent = `${doneSoFar} / ${studyTotal}`;
  els.studyProgressFill.style.width = `${(doneSoFar / studyTotal) * 100}%`;
}

// Grading is guarded two ways: `studyBusy` ignores a second click that
// lands while the first is still being processed (the actual fix for
// "pressing Got it very fast makes the button disappear" - a fast
// double-click was registering as two separate grades, which could burn
// through the last two cards on one tap and jump straight to the done
// screen), and the `if (!card) return` is a hard backstop so an edge case
// can never throw on `card.index` and silently abort mid-handler.
function gradeCard(quality) {
  if (studyBusy || studyQueue.length === 0) return;
  studyBusy = true;

  const card = studyQueue.shift();
  if (!card) { studyBusy = false; return; }

  updateCardSRS(studyDeckId, card.index, quality);
  if (quality < 3) {
    studyQueue.push(card); // "Review again" - retry later this session
    notGotten.set(card.index, card);
  } else {
    notGotten.delete(card.index); // got it this time - clear it off the missed list
    burstConfetti(18); // smaller than the deck-cleared celebration, still falls from the top
  }
  renderNotGottenTab();

  showNextStudyCard();
  requestAnimationFrame(() => { studyBusy = false; });
}

els.studyBtnKnown.addEventListener("click", () => gradeCard(4));
els.studyBtnReview.addEventListener("click", () => gradeCard(2));

// Tiny CSS-only confetti burst for "deck cleared" - no library, no images,
// and it inherits the global prefers-reduced-motion override at the top
// of style.css automatically since it's just a CSS animation.
function burstConfetti(count = 40) {
  const colors = ["#FFD23F", "#2A6F72", "#B5482A", "#211F1B"];
  const container = document.createElement("div");
  container.className = "confetti-burst";
  for (let i = 0; i < count; i++) {
    const piece = document.createElement("span");
    piece.style.setProperty("--startX", `${Math.random() * 100}vw`);
    piece.style.setProperty("--drift", `${(Math.random() - 0.5) * 200}px`);
    piece.style.setProperty("--rot", `${(Math.random() - 0.5) * 900}deg`);
    piece.style.setProperty("--delay", `${Math.random() * 400}ms`);
    piece.style.setProperty("--duration", `${1.1 + Math.random() * 0.9}s`);
    piece.style.background = colors[i % colors.length];
    container.appendChild(piece);
  }
  document.body.appendChild(container);
  setTimeout(() => container.remove(), 2400);
}

function escapeHtml(str) {
  return str.replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

// ---------- Init ----------
updateStreakForVisit();
refreshUsageBadge();
renderDeckList();
