import os
import re
from collections import defaultdict
from datetime import date

import fitz  # PyMuPDF
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound

from chunker import (
    chunk_transcript,
    chunk_sections,
    semantic_chunk_sections,
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

# Both chunking strategies get their own daily cap, checked independently -
# 1 free use each per day, by default. Not tied to any paid-tier system
# (there isn't one yet) - purely a cost/abuse guardrail while this runs on
# free hosting. Override via env vars for local development, where hitting
# a wall of 1 use per session would make testing painful - e.g. set
# FIXED_DAILY_LIMIT=999 and SEMANTIC_DAILY_LIMIT=999 in your local .env,
# and leave the strict defaults for the deployed instance.
FIXED_DAILY_LIMIT = int(os.environ.get("FIXED_DAILY_LIMIT", 1))
SEMANTIC_DAILY_LIMIT = int(os.environ.get("SEMANTIC_DAILY_LIMIT", 1))

app = FastAPI(title="MicroChunk API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your exact Pages URL before going live
    allow_methods=["*"],
    allow_headers=["*"],
)

_usage: dict[str, dict] = defaultdict(
    lambda: {"date": None, "used_mb": 0.0, "semantic_count": 0, "fixed_count": 0}
)


def _client_key(request: Request) -> str:
    # SECURITY: this used to take the FIRST value in X-Forwarded-For, which
    # is exactly the part a client can set to whatever they want - trivially
    # defeating the quota by sending a fresh fake value on every request.
    # A standard reverse proxy (Render's edge, in front of this app) APPENDS
    # the real connecting IP to the end of the chain rather than replacing
    # it, so the trustworthy value is the LAST one, not the first. This
    # assumes exactly one trusted proxy hop in front of the app (true for
    # Render's default setup) - if you ever add another proxy/CDN layer in
    # front of Render, this needs revisiting.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _quota_snapshot(record: dict) -> dict:
    remaining_mb = max(0.0, DAILY_MB_LIMIT - record["used_mb"])
    remaining_semantic = max(0, SEMANTIC_DAILY_LIMIT - record["semantic_count"])
    remaining_fixed = max(0, FIXED_DAILY_LIMIT - record["fixed_count"])
    return {
        "used_mb": round(record["used_mb"], 2),
        "limit_mb": DAILY_MB_LIMIT,
        "remaining_mb": round(remaining_mb, 2),
        "semantic_used": record["semantic_count"],
        "semantic_limit": SEMANTIC_DAILY_LIMIT,
        "semantic_remaining": remaining_semantic,
        "fixed_used": record["fixed_count"],
        "fixed_limit": FIXED_DAILY_LIMIT,
        "fixed_remaining": remaining_fixed,
    }


def _get_today_record(request: Request) -> dict:
    key = _client_key(request)
    today = date.today().isoformat()
    record = _usage[key]
    if record["date"] != today:
        record["date"] = today
        record["used_mb"] = 0.0
        record["semantic_count"] = 0
        record["fixed_count"] = 0
    return record


def _check_and_charge(request: Request, mb_cost: float) -> dict:
    record = _get_today_record(request)

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


def _record_semantic_use(request: Request):
    record = _get_today_record(request)
    record["semantic_count"] += 1


def _record_fixed_use(request: Request):
    record = _get_today_record(request)
    record["fixed_count"] += 1


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
    """Lets the frontend show remaining MB and per-strategy uses without spending any."""
    key = _client_key(request)
    today = date.today().isoformat()
    record = _usage[key]
    if record["date"] != today:
        return _quota_snapshot({"used_mb": 0.0, "semantic_count": 0, "fixed_count": 0})
    return _quota_snapshot(record)


@app.post("/api/pdf")
async def chunk_pdf(
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

    # Both strategies are capped at their own daily allowance. If the one
    # requested is spent, try the other before giving up entirely - a demo
    # should degrade gracefully, not hard-fail just because you already
    # tried one mode a moment ago. Only errors out if BOTH are spent.
    today_record = _get_today_record(request)
    fixed_ok = today_record["fixed_count"] < FIXED_DAILY_LIMIT
    semantic_ok = today_record["semantic_count"] < SEMANTIC_DAILY_LIMIT

    if strategy == "fixed" and not fixed_ok and semantic_ok:
        effective_strategy = "semantic"
    elif strategy == "semantic" and not semantic_ok and fixed_ok:
        effective_strategy = "fixed"
    elif (strategy == "fixed" and fixed_ok) or (strategy == "semantic" and semantic_ok):
        effective_strategy = strategy
    else:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Today's free chunking uses are spent "
                f"({FIXED_DAILY_LIMIT} structural + {SEMANTIC_DAILY_LIMIT} semantic). "
                f"Come back tomorrow!"
            ),
        )

    used_semantic = False
    fallback_reason = None
    if effective_strategy != strategy:
        fallback_reason = f"'{strategy}' mode's free use for today is spent - used {effective_strategy} instead."

    try:
        with fitz.open(stream=raw, filetype="pdf") as pdf:
            page_count = pdf.page_count

            def page_blocks_iter():
                for page in pdf:
                    raw_blocks = page.get_text("blocks")
                    ordered = sorted(raw_blocks, key=lambda b: (round(b[1] / 5), b[0]))
                    yield [b[4] for b in ordered if b[6] == 0]

            if effective_strategy == "semantic":
                try:
                    chunks = semantic_chunk_sections(page_blocks_iter(), max_words=words_per_chunk)
                    used_semantic = True
                except Exception as exc:
                    # A demo should never just break because the embedding
                    # model failed to load or run - fall back to the
                    # reliable structural chunker and say so honestly,
                    # rather than surfacing a raw error mid-presentation.
                    fallback_reason = f"semantic chunking failed ({exc}), used structural chunking instead"
                    chunks = list(chunk_sections(page_blocks_iter(), words_per_chunk=words_per_chunk))
            else:
                chunks = list(chunk_sections(page_blocks_iter(), words_per_chunk=words_per_chunk))

            if not chunks:
                raise HTTPException(
                    status_code=422,
                    detail="No extractable text found - this PDF may be scanned images without a text layer.",
                )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Couldn't read that PDF: {exc}")

    # Recorded exactly once, only for the mode that actually ran, and only
    # on success - a failed attempt shouldn't burn today's one free use.
    if used_semantic:
        _record_semantic_use(request)
    else:
        _record_fixed_use(request)
    quota = _quota_snapshot(_get_today_record(request))

    total_words = sum(c["word_count"] for c in chunks)
    return {
        "source": "pdf",
        "filename": file.filename,
        "strategy": "semantic" if used_semantic else "structural",
        "requested_strategy": strategy,
        "fallback_reason": fallback_reason,
        "page_count": page_count,
        "total_words": total_words,
        "chunk_count": len(chunks),
        "chunks": chunks,
        "usage": quota,
    }


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
