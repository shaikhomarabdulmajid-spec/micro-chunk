// Point this at wherever you deploy the FastAPI backend (e.g. DigitalOcean).
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
  ytInput: document.getElementById("yt-input"),
  ytSubmit: document.getElementById("yt-submit"),
  wordsRange: document.getElementById("words-per-chunk"),
  wordsVal: document.getElementById("words-per-chunk-val"),
  status: document.getElementById("status"),
  deckSection: document.getElementById("deck-section"),
  deckTitle: document.getElementById("deck-title"),
  deckMeta: document.getElementById("deck-meta"),
  deck: document.getElementById("deck"),
};

let activeTab = "pdf";

els.tabs.forEach(tab => {
  tab.addEventListener("click", () => {
    activeTab = tab.dataset.tab;
    els.tabs.forEach(t => t.classList.toggle("active", t === tab));
    Object.entries(els.panels).forEach(([key, panel]) =>
      panel.classList.toggle("active", key === activeTab)
    );
    setStatus("");
  });
});

els.wordsRange.addEventListener("input", () => {
  els.wordsVal.textContent = els.wordsRange.value;
});

function setStatus(message, kind) {
  els.status.textContent = message;
  els.status.className = "status" + (kind ? " " + kind : "");
}

// ---------- PDF intake ----------
els.dropzone.addEventListener("dragover", e => {
  e.preventDefault();
  els.dropzone.classList.add("drag");
});
els.dropzone.addEventListener("dragleave", () => els.dropzone.classList.remove("drag"));
els.dropzone.addEventListener("drop", e => {
  e.preventDefault();
  els.dropzone.classList.remove("drag");
  const file = e.dataTransfer.files[0];
  if (file) handlePdf(file);
});
els.pdfInput.addEventListener("change", () => {
  const file = els.pdfInput.files[0];
  if (file) handlePdf(file);
});

async function handlePdf(file) {
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    setStatus("That doesn't look like a PDF.", "error");
    return;
  }
  els.dzTitle.textContent = file.name;
  setStatus("Reading and chunking " + file.name + " …", "busy");

  const formData = new FormData();
  formData.append("file", file);

  try {
    const wordsPerChunk = els.wordsRange.value;
    const res = await fetch(
      `${API_BASE_URL}/api/pdf?words_per_chunk=${wordsPerChunk}`,
      { method: "POST", body: formData }
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Something went wrong.");

    renderDeck(data, {
      title: file.name.replace(/\.pdf$/i, ""),
      meta: `${data.page_count} pages · ${data.total_words.toLocaleString()} words · ${data.chunk_count} cards`,
    });
    setStatus(`Done — ${data.chunk_count} cards ready below.`);
  } catch (err) {
    setStatus(err.message, "error");
  }
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
  els.ytSubmit.disabled = true;

  try {
    const wordsPerChunk = Number(els.wordsRange.value);
    const res = await fetch(`${API_BASE_URL}/api/youtube`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, words_per_chunk: wordsPerChunk }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Something went wrong.");

    renderDeck(data, {
      title: `youtu.be/${data.video_id}`,
      meta: `${data.total_words.toLocaleString()} words · ${data.chunk_count} cards`,
    });
    setStatus(`Done — ${data.chunk_count} cards ready below.`);
  } catch (err) {
    setStatus(err.message, "error");
  } finally {
    els.ytSubmit.disabled = false;
  }
}

// ---------- Rendering ----------
function renderDeck(data, { title, meta }) {
  els.deckTitle.textContent = title;
  els.deckMeta.textContent = meta;
  els.deck.innerHTML = "";

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
