# MicroChunk

Turns a PDF or YouTube video into fixed-length, sentence-clean "study cards."
No paid AI APIs anywhere — PDF text extraction and YouTube captions are both
pulled and processed locally/on your own server.

```
microchunk/
├── backend/          FastAPI app (Python) — chunking logic, no external AI calls
│   ├── main.py
│   ├── chunker.py
│   └── requirements.txt
└── frontend/          Static HTML/CSS/JS — no build step
    ├── index.html
    ├── style.css
    └── app.js
```

## How the "no API" part works
- **PDF** → `pdfplumber` extracts text directly from the file, entirely offline.
- **YouTube** → `youtube-transcript-api` reads the video's *existing* caption
  track (auto or manual) straight from YouTube's caption endpoint. No API key,
  no official Data API, no cost. Videos with captions turned off won't work —
  that's the one real limitation of staying API-free (fixing it would mean
  running local Whisper transcription, which we intentionally left out of v1
  to keep hosting light).
- **Chunking** → fixed word-count windows, snapped to sentence boundaries so
  a card never cuts a sentence in half. Pure Python/regex, no model.

## Run it locally

```bash
# backend
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# frontend (separate terminal)
cd frontend
python3 -m http.server 8080
```

Open `http://localhost:8080`. The frontend calls `API_BASE_URL` in `app.js`
(currently `http://localhost:8000`) — update that constant once you deploy
the backend somewhere real.

## Deploying with your GitHub Student Developer Pack

**Frontend → GitHub Pages (free)**
1. Push this repo to GitHub.
2. Repo Settings → Pages → set the source to the `frontend/` folder (or move
   its contents to a `docs/` folder or the repo root, since Pages needs a
   specific folder — `frontend/` won't work directly unless you use a
   GitHub Action to publish it. Simplest path: rename `frontend/` to `docs/`
   and point Pages at `/docs`).
3. Your site goes live at `https://<username>.github.io/<repo>/`.

**Backend → DigitalOcean ($200 Student Pack credit)**
1. Activate the DigitalOcean offer from your GitHub Education benefits page.
2. Easiest option: **App Platform** (no server management).
   - New App → connect your GitHub repo → set the source directory to
     `backend/`.
   - Build command: `pip install -r requirements.txt`
   - Run command: `uvicorn main:app --host 0.0.0.0 --port 8080`
   - App Platform's cheapest always-on instance runs well within your credit
     for months.
3. Copy the resulting URL (e.g. `https://microchunk-api-xxxxx.ondigitalocean.app`)
   into `API_BASE_URL` in `frontend/app.js`, commit, and Pages will pick it up.
4. In `backend/main.py`, tighten `allow_origins=["*"]` to your exact Pages URL
   before calling it launched — wide-open CORS is fine for testing, not for
   production.

## Notes / known limits
- PDFs that are scanned images (no text layer) won't extract anything — that
  would need OCR, which is a reasonable v2 addition (`pytesseract`, still
  fully local/free).
- Very large PDFs (near the 50MB cap) will take a few seconds to parse; that's
  expected and happens server-side.
- 50MB upload limit and video-caption requirement are enforced in
  `backend/main.py` if you want to change either.
