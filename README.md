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
(currently `http://localhost:8000`) — remember to update to Actual hosted site.

