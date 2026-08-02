import os
import re
import threading
import time
import uuid
from collections import defaultdict
from datetime import date

import fitz  # PyMuPDF
from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound

from chunker import (
    chunk_transcript,
    chunk_pages_streaming,
    semantic_chunk_text,
    DEFAULT_WORDS_PER_CHUNK,
)

# 10MB hard ceiling on any single upload (separate from the daily MB quota
# below - this just rejects one absurdly large file outright).
MAX_PDF_BYTES = 10 * 1024 * 1024

# ---------------------------------------------------------------------------
# Daily quota, now measured in MB actually processed rather than a flat
# job count. The reasoning: a count-based limit (e.g. "8 jobs/day") doesn't
# actually protect the server, since 8 jobs at the 10MB ceiling is still 80MB
# of parsing work from one visitor. Charging by MB means the quota tracks
# the thing that actually costs CPU/time, not an arbitrary click count.
#
# In-memory, per-IP, resets at midnight UTC, and resets on a server restart
# (Render sleep/wake does NOT clear this - only a redeploy or crash does).
# ---------------------------------------------------------------------------
DAILY_MB_LIMIT = float(os.environ.get("DAILY_MB_LIMIT", 40))

# YouTube jobs don't have a meaningful "file size" (a caption track is tiny
# regardless of video length), so they're charged a small flat MB-equivalent
# just to bound request *frequency*, not because they cost 1MB of real work.
YOUTUBE_JOB_COST_MB = float(os.environ.get("YOUTUBE_JOB_COST_MB", 1.0))

app = FastAPI(title="MicroChunk API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your exact Pages URL before going live
    allow_methods=["*"],
    allow_headers=["*"],
)

_usage: dict[str, dict] = defaultdict(lambda: {"date": None, "used_mb": 0.0})


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _quota_snapshot(record: dict) -> dict:
    remaining = max(0.0, DAILY_MB_LIMIT - record["used_mb"])
    return {
        "used_mb": round(record["used_mb"], 2),
        "limit_mb": DAILY_MB_LIMIT,
        "remaining_mb": round(remaining, 2),
    }


def _check_and_charge(request: Request, mb_cost: float) -> dict:
    key = _client_key(request)
    today = date.today().isoformat()
    record = _usage[key]

    if record["date"] != today:
        record["date"] = today
        record["used_mb"] = 0.0

    if record["used_mb"] + mb_cost > DAILY_MB_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Daily free limit of {DAILY_MB_LIMIT}MB reached "
                f"({record['used_mb']:.1f}MB used). Come back tomorrow!"
            ),
        )

    record["used_mb"] += mb_cost
    return _quota_snapshot(record)


class YouTubeRequest(BaseModel):
    url: str
    words_per_chunk: int = DEFAULT_WORDS_PER_CHUNK
    strategy: str = "fixed"


def extract_video_id(url: str) -> str:
    patterns = [r"(?:v=|\/videos\/|embed\/|youtu\.be\/|\/v\/|\/shorts\/)([a-zA-Z0-9_-]{11})"]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{11}", url.strip()):
        return url.strip()
    raise HTTPException(status_code=400, detail="Could not find a video ID in that URL.")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/usage")
def usage(request: Request):
    """Lets the frontend show 'X MB left today' without spending any."""
    key = _client_key(request)
    today = date.today().isoformat()
    record = _usage[key]
    used_mb = record["used_mb"] if record["date"] == today else 0.0
    return _quota_snapshot({"used_mb": used_mb})


# ---------------------------------------------------------------------------
# PDF jobs: background thread + polling, so the frontend can show REAL
# per-page progress instead of a spinner that just sits there. PyMuPDF and
# the (optional) embedding model are both synchronous/CPU-bound, so this
# work runs in a plain thread rather than blocking the async event loop -
# that keeps /api/usage and other requests responsive while a big PDF is
# being chunked.
#
# _jobs is a simple in-memory dict, pruned of anything older than 15
# minutes on every new job creation. No database, no queue - proportional
# to what this app actually needs.
# ---------------------------------------------------------------------------
_jobs: dict[str, dict] = {}
_JOB_TTL_SECONDS = 15 * 60


def _prune_old_jobs():
    cutoff = time.time() - _JOB_TTL_SECONDS
    stale = [jid for jid, job in _jobs.items() if job["created_at"] < cutoff]
    for jid in stale:
        _jobs.pop(jid, None)


def _run_pdf_job(job_id: str, raw: bytes, words_per_chunk: int, strategy: str):
    job = _jobs[job_id]
    try:
        with fitz.open(stream=raw, filetype="pdf") as pdf:
            page_count = pdf.page_count
            job["total_pages"] = page_count

            if strategy == "semantic":
                text_parts = []
                for i, page in enumerate(pdf):
                    text_parts.append(page.get_text() or "")
                    # Extraction is the first half of semantic mode's work.
                    job["phase"] = "reading pages"
                    job["percent"] = round(((i + 1) / page_count) * 50, 1)
                full_text = "\n".join(text_parts).strip()
                if not full_text:
                    raise ValueError(
                        "No extractable text found - this PDF may be scanned images without a text layer."
                    )

                def on_embed_progress(done_batches, total_batches):
                    job["phase"] = "grouping by topic"
                    job["percent"] = round(50 + (done_batches / total_batches) * 50, 1)

                chunks = semantic_chunk_text(
                    full_text, max_words=words_per_chunk, on_progress=on_embed_progress
                )
            else:
                def page_text_iter():
                    for i, page in enumerate(pdf):
                        yield page.get_text() or ""
                        job["phase"] = "chunking"
                        job["percent"] = round(((i + 1) / page_count) * 100, 1)

                chunks = list(chunk_pages_streaming(page_text_iter(), words_per_chunk=words_per_chunk))
                if not chunks:
                    raise ValueError(
                        "No extractable text found - this PDF may be scanned images without a text layer."
                    )

        total_words = sum(c["word_count"] for c in chunks)
        job["status"] = "done"
        job["percent"] = 100
        job["result"] = {
            "source": "pdf",
            "filename": job["filename"],
            "strategy": strategy,
            "page_count": page_count,
            "total_words": total_words,
            "chunk_count": len(chunks),
            "chunks": chunks,
            "usage": job["usage"],
        }
    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc)


@app.post("/api/pdf/start")
async def start_pdf_job(
    request: Request,
    file: UploadFile = File(...),
    words_per_chunk: int = DEFAULT_WORDS_PER_CHUNK,
    strategy: str = "fixed",
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a .pdf file.")
    if strategy not in ("fixed", "semantic"):
        raise HTTPException(status_code=400, detail="strategy must be 'fixed' or 'semantic'.")

    raw = await file.read()
    if len(raw) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="PDF is larger than the 10MB limit.")

    mb_cost = len(raw) / (1024 * 1024)
    quota = _check_and_charge(request, mb_cost)

    _prune_old_jobs()
    job_id = uuid.uuid4().hex
    _jobs[job_id] = {
        "status": "processing",
        "phase": "starting",
        "percent": 0,
        "total_pages": None,
        "filename": file.filename,
        "usage": quota,
        "created_at": time.time(),
        "result": None,
        "error": None,
    }

    thread = threading.Thread(
        target=_run_pdf_job, args=(job_id, raw, words_per_chunk, strategy), daemon=True
    )
    thread.start()

    return {"job_id": job_id, "usage": quota}


@app.get("/api/pdf/progress/{job_id}")
def pdf_progress(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown or expired job.")

    payload = {
        "status": job["status"],
        "phase": job["phase"],
        "percent": job["percent"],
        "total_pages": job["total_pages"],
    }
    if job["status"] == "done":
        payload["result"] = job["result"]
    elif job["status"] == "error":
        payload["detail"] = job["error"]
    return payload


@app.post("/api/youtube")
def chunk_youtube(req: YouTubeRequest, request: Request):
    video_id = extract_video_id(req.url)

    if req.strategy != "fixed":
        raise HTTPException(
            status_code=400,
            detail="Only strategy='fixed' is supported for YouTube transcripts right now.",
        )

    quota = _check_and_charge(request, YOUTUBE_JOB_COST_MB)

    try:
        segments = YouTubeTranscriptApi.get_transcript(video_id)
    except TranscriptsDisabled:
        raise HTTPException(status_code=422, detail="Captions are disabled for this video.")
    except NoTranscriptFound:
        raise HTTPException(status_code=422, detail="No captions found for this video.")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Couldn't fetch a transcript: {exc}")

    if not segments:
        raise HTTPException(status_code=422, detail="This video has an empty transcript.")

    chunks = chunk_transcript(segments, words_per_chunk=req.words_per_chunk)
    total_words = sum(c["word_count"] for c in chunks)

    return {
        "source": "youtube",
        "video_id": video_id,
        "total_words": total_words,
        "chunk_count": len(chunks),
        "chunks": chunks,
        "usage": quota,
    }
