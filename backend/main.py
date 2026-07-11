import io
import re

import pdfplumber
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound

from chunker import chunk_text, chunk_transcript, DEFAULT_WORDS_PER_CHUNK

app = FastAPI(title="MicroChunk API")

# Allow the GitHub Pages frontend (and local dev) to call this API directly.
# Tighten allow_origins to your exact Pages URL before going live.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class YouTubeRequest(BaseModel):
    url: str
    words_per_chunk: int = DEFAULT_WORDS_PER_CHUNK


def extract_video_id(url: str) -> str:
    patterns = [
        r"(?:v=|\/videos\/|embed\/|youtu\.be\/|\/v\/|\/shorts\/)([a-zA-Z0-9_-]{11})",
    ]
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


@app.post("/api/pdf")
async def chunk_pdf(file: UploadFile = File(...), words_per_chunk: int = DEFAULT_WORDS_PER_CHUNK):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a .pdf file.")

    raw = await file.read()
    if len(raw) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="PDF is larger than the 50MB limit.")

    text_parts = []
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text_parts.append(page_text)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Couldn't read that PDF: {exc}")

    full_text = "\n".join(text_parts).strip()
    if not full_text:
        raise HTTPException(
            status_code=422,
            detail="No extractable text found - this PDF may be scanned images without a text layer.",
        )

    chunks = chunk_text(full_text, words_per_chunk=words_per_chunk)
    return {
        "source": "pdf",
        "filename": file.filename,
        "page_count": page_count,
        "total_words": len(full_text.split()),
        "chunk_count": len(chunks),
        "chunks": chunks,
    }


@app.post("/api/youtube")
def chunk_youtube(req: YouTubeRequest):
    video_id = extract_video_id(req.url)

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
    }
