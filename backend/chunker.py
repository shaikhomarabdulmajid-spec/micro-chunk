"""
Local, API-free text chunking.

Strategy: fixed-size windows, but snapped to sentence boundaries so a chunk
never cuts a sentence in half. Everything here runs on-device with the
standard library plus regex - no network calls, no model downloads.
"""
import re

# Matches sentence-ending punctuation followed by whitespace + a capital
# letter / quote / number, while trying not to split on common abbreviations.
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


def chunk_transcript(segments: list[dict], words_per_chunk: int = DEFAULT_WORDS_PER_CHUNK) -> list[dict]:
    """
    Chunk a YouTube transcript (list of {text, start, duration} segments from
    youtube-transcript-api) into fixed-size windows, preserving start/end
    timestamps for each chunk so the UI can deep-link into the video.
    """
    full_text_parts = []
    boundaries = []  # (char_start, char_end, timestamp_start)
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
