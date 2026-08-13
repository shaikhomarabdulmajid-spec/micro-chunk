# MicroChunk

<img width="1875" height="842" alt="microChunk - README Picture" src="https://github.com/user-attachments/assets/da74a669-d60b-4371-ba8c-d5b39311bc2b" />

Turn a dense PDF or a YouTube video into small, topic-organized study cards
you can actually review - no paid AI APIs, ever.

## What problem does this solve?

Course PDFs and lecture videos aren't built for studying - they're built
for reading start to finish. A 40-page handbook or a 45-minute lecture
doesn't break itself into reviewable pieces. You either reread the whole
thing before a test or you don't review it at all. MicroChunk takes that
raw material and turns it into a deck of short, topic-labeled cards you
can actually work through and quiz yourself on, without having to make
the cards by hand.

## Why should I care?

- It's free to run and free to use - no paid API calls, no per-user fees,
  no account needed.
- It's not just a nicer way to read your notes. Study Mode makes you
  actually quiz yourself - mark a card "Got it" or "Review again," and the
  ones you don't know keep coming back until you've genuinely got them.
  That's the same idea behind why something like Anki works, not just a
  reformatted version of the same content.
- Nothing leaves your control. The PDF parsing and the YouTube caption
  pulling both run on a server you own - no third-party AI company ever
  sees the document. That matters if a teacher cares about student data
  or copyrighted course material.

## How does it work?

1. You upload a PDF or paste a YouTube link.
2. For a PDF, the backend reads it page by page and figures out the
   structure itself - short lines with no punctuation (like "Chapter 3"
   or a bolded title) get pulled out as section headings instead of
   turning into a card. Paragraphs under one heading get packed into
   cards up to a target size, and a card never mixes text from two
   different paragraphs or crosses into the next topic. All of that is
   plain rule-based logic - regex and structure, not a model.
3. For YouTube, the existing caption track gets pulled straight from the
   video (no audio download, no transcription) and chunked the same way,
   with each card keeping a timestamp back into the video.
4. The deck shows up as a sidebar of topics, each collapsed to just its
   heading until you open it, and that topic's cards lay out left to
   right so you can flip through them.
5. There's also an optional mode that groups text by topic *similarity*
   using a small local embedding model instead of paragraph structure -
   that's the one genuinely AI-ish piece here, and it's capped and falls
   back automatically to the rule-based method if it's ever unavailable,
   so a demo doesn't break because of it.
6. Decks get saved locally so you can come back tomorrow and actually
   have something to review - Study Mode uses real spaced repetition
   (SM-2, the same scheduling Anki runs on), so a card you know well
   shows up again in a week instead of every single day.

## What technologies did you use?

- Backend: Python, FastAPI, PyMuPDF for PDF text extraction,
  `youtube-transcript-api` for captions, and optionally
  `sentence-transformers` for the local topic-similarity model
- Frontend: plain HTML/CSS/JS - no framework, no build step
- Hosting: GitHub Pages for the frontend (free), Render for the backend
  (free tier)

## How do I run it?

```bash
# backend
cd backend
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000

# frontend, separate terminal
cd frontend
python -m http.server 8080
```
Then open `http://localhost:8080`. Daily usage limits and other settings
are environment variables at the top of `backend/main.py`.

## What did I learn (so far)?

- Grouping content by topic doesn't need AI. The part of this app that
  actually does the organizing is plain structural logic - the lesson
  that stuck was to reach for the simplest deterministic solution first
  and only treat a model as an optional extra, especially for something
  that has to work reliably in front of people.
- You can't trust anything the client sends you. The usage limiter used
  to read a request header a client can set to whatever it wants, which
  meant anyone could reset their own quota just by sending a fake value.
  Fixing it meant actually understanding how a reverse proxy modifies
  that header, and then proving the fix against a simulated spoofing
  attempt instead of just assuming it worked.
- The library you pick for something as "boring" as PDF parsing can make
  a huge difference. Swapping the extraction library cut processing time
  on an image-heavy document by around 17x in testing - not something
  you'd find without actually profiling real files.
- A fallback is worth more than a fancier feature. If the optional ML
  mode ever fails to load, the app quietly falls back to the reliable
  method instead of throwing an error - because this has to keep working
  even when I'm not the one driving it.
