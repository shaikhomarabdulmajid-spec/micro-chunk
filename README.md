# MicroChunk

<img width="1875" height="842" alt="microChunk - README Picture" src="https://github.com/user-attachments/assets/da74a669-d60b-4371-ba8c-d5b39311bc2b" />


Turn a dense PDF or a YouTube video into small, topic-organized study cards
you can actually review — no paid AI APIs, ever.

## What problem does this solve?

Course PDFs and lecture videos are built for reading start-to-finish, not
for studying. A 40-page handbook or a 45-minute lecture doesn't break
itself into reviewable pieces — you either read the whole thing again
before a test, or you don't review it at all. MicroChunk takes that raw
material and turns it into a deck of short, topic-labeled cards you can
actually work through and quiz yourself on, the way a flashcard app
expects content to look, without you having to manually make the cards.

## Why should I care?

- **It costs nothing to run or use.** No paid AI API calls, no per-user
  fees, no account required.
- **Active recall, not just reformatting.** Study Mode turns the deck into
  a real self-quiz: mark each card "Got it" or "Review again," and the
  ones you don't know keep resurfacing until you've actually cleared them
  — this is the same underlying principle (retrieval practice) that makes
  tools like Anki effective, not just a prettier way to read notes.
- **Nothing leaves your control.** PDF parsing and YouTube caption fetching
  both happen on the server you run — no third-party AI service ever sees
  the document content, which matters if a teacher cares about student
  data or copyrighted course material touching an external company.

## How does it work?

1. You upload a PDF or paste a YouTube link.
2. **PDF:** the backend reads the file page by page and groups its text
   into topics using the document's own structure — short, unpunctuated
   lines (like "Chapter 3" or a bolded title) are detected as headings and
   used to label a section, never turned into a card themselves. Paragraphs
   under one heading are packed into cards up to a target size; a card
   never mixes text from two different paragraphs or crosses a topic
   boundary. This is deterministic, rule-based logic — no model involved.
3. **YouTube:** the existing caption track is pulled directly from
   YouTube (no audio download, no transcription step) and chunked the
   same way, with each card keeping a timestamp back into the video.
4. The frontend renders the result as a sidebar of topics, each collapsed
   to just its heading until you open it, with that topic's cards laid
   out left-to-right so you can flip through them.
5. Optionally, a small local embedding model can group text by topic
   *similarity* instead of paragraph structure — this is the one piece
   that's genuinely machine learning (not an API call), and it's capped
   and falls back automatically to the rule-based method if it's
   unavailable, so a demo never breaks because of it.

## What technologies were used?

- **Backend:** Python, FastAPI, PyMuPDF (PDF text extraction),
  `youtube-transcript-api` (caption retrieval), optionally
  `sentence-transformers` for the local topic-similarity model
- **Frontend:** vanilla HTML/CSS/JS — no framework, no build step
- **Hosting:** GitHub Pages (frontend, free) + Render (backend, free tier)

## How do you run it?

```bash
# backend
cd backend
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000

# frontend (separate terminal)
cd frontend
python -m http.server 8080
```
Open `http://localhost:8080`. See `backend/main.py` for the environment
variables that control daily usage limits.

## What did I learn (so far)?

- **Grouping content by topic doesn't require AI.** The actual "smart"
  part of this app — organizing a document into labeled sections — is
  pure structural/regex logic. The lesson that stuck: reach for the
  simplest deterministic solution first, and treat a model as an
  optional refinement, not the foundation, especially for anything that
  needs to work reliably in front of an audience.
- **Client-supplied data can't be trusted for anything that matters.**
  The usage limiter originally read a request header that a client can
  set to whatever they want, which would have let anyone reset their
  quota on every request. Fixing it meant understanding exactly how a
  reverse proxy modifies that header, and verifying the fix against a
  simulated spoofing attempt rather than assuming it was correct.
- **Library choice has a real, measurable performance cost.** Swapping
  the PDF text-extraction library cut processing time on an image-heavy
  document by roughly 17x in testing.
- **A resilient fallback matters more than a fancier feature.** The
  system is built so that if the optional ML-based mode fails for any
  reason, it degrades automatically to the reliable method instead of
  erroring out.
