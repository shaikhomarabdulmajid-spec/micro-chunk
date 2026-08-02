"""
Local, API-free text chunking.

Two strategies, both sentence-boundary safe:
  - "fixed"    : fixed word-count windows. Cheap, deterministic, streams
                 page-by-page for large PDFs so memory stays bounded.
  - "semantic" : groups sentences by topical similarity using a small local
                 sentence-embedding model (all-MiniLM-L6-v2, ~90MB, runs on
                 CPU). Better card boundaries, costs more time/RAM, and
                 needs the whole document's sentences in memory at once -
                 so it's opt-in, not the default for huge files.

Nothing here calls out to a network AI API. The "semantic" model is
downloaded once (on first use) and then runs entirely on your own server.
"""
import re

_SENTENCE_SPLIT = re.compile(
    r'(?<!\b[A-Z][a-z]\.)(?<!\bMr\.)(?<!\bMrs\.)(?<!\bDr\.)(?<!\bvs\.)'
    r'(?<=[.!?])\s+(?=[A-Z0-9"\u201c])'
)

DEFAULT_WORDS_PER_CHUNK = 130
MIN_WORDS_PER_CHUNK = 40


def split_sentences(text: str) -> list[str]:
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        return []
    sentences = _SENTENCE_SPLIT.split(text)
    return [s.strip() for s in sentences if s.strip()]


def chunk_text(text: str, words_per_chunk: int = DEFAULT_WORDS_PER_CHUNK) -> list[dict]:
    """Greedily pack whole sentences into fixed-size (word-count) windows."""
    sentences = split_sentences(text)
    chunks = []
    current: list[str] = []
    current_words = 0

    for sentence in sentences:
        sentence_words = len(sentence.split())

        if current_words + sentence_words > words_per_chunk and current_words >= MIN_WORDS_PER_CHUNK:
            chunks.append(_finalize(current, len(chunks)))
            current, current_words = [], 0

        current.append(sentence)
        current_words += sentence_words

    if current:
        chunks.append(_finalize(current, len(chunks)))

    return chunks


def _finalize(sentences: list[str], index: int) -> dict:
    body = ' '.join(sentences)
    return {
        "index": index,
        "text": body,
        "word_count": len(body.split()),
        "sentence_count": len(sentences),
    }


# ---------------------------------------------------------------------------
# Semantic chunking (local embeddings, lazy-loaded so an idle server doesn't
# pay the RAM cost until someone actually requests strategy="semantic")
# ---------------------------------------------------------------------------
_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "Semantic chunking needs the optional dependencies in "
                "requirements-semantic.txt (sentence-transformers + torch). "
                "Install them, or use strategy='fixed' instead."
            ) from exc
        _embedder = SentenceTransformer("all-MiniLM-L6-v2")  # ~90MB, CPU-friendly
    return _embedder


def semantic_chunk_text(
    text: str,
    max_words: int = DEFAULT_WORDS_PER_CHUNK,
    similarity_threshold: float = 0.55,
    on_progress=None,
) -> list[dict]:
    """
    Group consecutive sentences by embedding similarity instead of raw word
    count. A chunk breaks when either (a) the topic visibly shifts (cosine
    similarity between neighboring sentences drops below the threshold) or
    (b) it would exceed max_words - the size cap is a guardrail, not the
    primary signal.

    on_progress(done_batches, total_batches), if given, is called after each
    embedding batch - this is what lets the API report real progress during
    the slowest part of semantic mode instead of just sitting at one state.
    """
    sentences = split_sentences(text)
    if len(sentences) <= 1:
        return chunk_text(text, max_words)

    model = _get_embedder()
    batch_size = 32
    total_batches = max(1, (len(sentences) + batch_size - 1) // batch_size)
    embeddings = []
    for i in range(0, len(sentences), batch_size):
        batch = sentences[i:i + batch_size]
        embeddings.extend(model.encode(batch, normalize_embeddings=True, show_progress_bar=False))
        if on_progress:
            on_progress(i // batch_size + 1, total_batches)

    chunks = []
    current = [sentences[0]]
    current_words = len(sentences[0].split())

    for i in range(1, len(sentences)):
        similarity = float(embeddings[i - 1] @ embeddings[i])  # normalized -> cosine sim
        sentence_words = len(sentences[i].split())
        topic_shift = similarity < similarity_threshold
        would_overflow = current_words + sentence_words > max_words

        if (topic_shift and current_words >= MIN_WORDS_PER_CHUNK) or would_overflow:
            chunks.append(_finalize(current, len(chunks)))
            current, current_words = [], 0

        current.append(sentences[i])
        current_words += sentence_words

    if current:
        chunks.append(_finalize(current, len(chunks)))

    return chunks


# ---------------------------------------------------------------------------
# Memory-bounded streaming chunking for large PDFs
# ---------------------------------------------------------------------------
def chunk_pages_streaming(pages, words_per_chunk: int = DEFAULT_WORDS_PER_CHUNK):
    """
    Consume an iterator of per-page text (e.g. pdfplumber's lazy `pdf.pages`)
    and yield finished chunks as soon as they're ready, instead of joining
    the entire document into one giant string first. Memory use stays
    proportional to one chunk's worth of text, not the whole PDF -
    a 900-page book costs the same RAM here as a 9-page handout.
    """
    buffer: list[str] = []
    buffer_words = 0
    index = 0
    carry = ""

    for page_text in pages:
        page_text = (carry + " " + page_text).strip() if carry else page_text.strip()
        if not page_text:
            continue

        sentences = split_sentences(page_text)
        if not sentences:
            continue

        if not re.search(r'[.!?"\u201d]$', page_text):
            carry = sentences.pop()
        else:
            carry = ""

        for sentence in sentences:
            words = len(sentence.split())
            if buffer_words + words > words_per_chunk and buffer_words >= MIN_WORDS_PER_CHUNK:
                yield _finalize(buffer, index)
                index += 1
                buffer, buffer_words = [], 0
            buffer.append(sentence)
            buffer_words += words

    if carry:
        buffer.append(carry)
        buffer_words += len(carry.split())
    if buffer:
        yield _finalize(buffer, index)


def chunk_transcript(segments: list[dict], words_per_chunk: int = DEFAULT_WORDS_PER_CHUNK) -> list[dict]:
    """
    Chunk a YouTube transcript (list of {text, start, duration} segments from
    youtube-transcript-api) into fixed-size windows, preserving start/end
    timestamps for each chunk so the UI can deep-link into the video.
    """
    full_text_parts = []
    boundaries = []
    cursor = 0

    for seg in segments:
        piece = seg["text"].strip().replace("\n", " ")
        if not piece:
            continue
        if full_text_parts:
            full_text_parts.append(" ")
            cursor += 1
        start_char = cursor
        full_text_parts.append(piece)
        cursor += len(piece)
        boundaries.append((start_char, cursor, seg["start"], seg["start"] + seg.get("duration", 0)))

    full_text = "".join(full_text_parts)
    sentences_with_span = _split_sentences_with_spans(full_text)

    chunks = []
    current_sents = []
    current_words = 0
    chunk_start_char = None

    def seg_time_for_char(char_pos: int, end: bool = False) -> float:
        for s, e, t0, t1 in boundaries:
            if s <= char_pos <= e:
                return t1 if end else t0
        return boundaries[-1][3] if boundaries else 0.0

    for sent_text, s_start, s_end in sentences_with_span:
        words = len(sent_text.split())
        if chunk_start_char is None:
            chunk_start_char = s_start

        if current_words + words > words_per_chunk and current_words >= MIN_WORDS_PER_CHUNK:
            chunks.append(_finalize_transcript_chunk(
                current_sents, len(chunks), chunk_start_char, seg_time_for_char, seg_time_for_char
            ))
            current_sents = []
            current_words = 0
            chunk_start_char = s_start

        current_sents.append((sent_text, s_start, s_end))
        current_words += words

    if current_sents:
        chunks.append(_finalize_transcript_chunk(
            current_sents, len(chunks), chunk_start_char, seg_time_for_char, seg_time_for_char
        ))

    return chunks


def _split_sentences_with_spans(text: str) -> list[tuple[str, int, int]]:
    spans = []
    for m in re.finditer(r'\S.*?(?<=[.!?])(?=\s|$)|\S+$', text):
        s = m.group().strip()
        if s:
            spans.append((s, m.start(), m.end()))
    if not spans and text.strip():
        spans = [(text.strip(), 0, len(text))]
    return spans


def _finalize_transcript_chunk(sents, index, start_char, time_fn_start, time_fn_end) -> dict:
    body = ' '.join(s[0] for s in sents)
    start_time = time_fn_start(sents[0][1])
    end_time = time_fn_end(sents[-1][2] - 1 if sents[-1][2] > 0 else sents[-1][2])
    return {
        "index": index,
        "text": body,
        "word_count": len(body.split()),
        "sentence_count": len(sents),
        "start_time": round(start_time, 1),
        "end_time": round(end_time, 1),
    }
