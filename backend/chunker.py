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

DEFAULT_WORDS_PER_CHUNK = 90
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


def _finalize(text_pieces: list[str], index: int, heading: str = None) -> dict:
    body = ' '.join(text_pieces)
    return {
        "index": index,
        "text": body,
        "word_count": len(body.split()),
        "sentence_count": len(split_sentences(body)),
        "heading": heading,
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


def semantic_chunk_sections(
    pages_blocks,
    max_words: int = DEFAULT_WORDS_PER_CHUNK,
    similarity_threshold: float = 0.55,
    on_progress=None,
) -> list[dict]:
    """
    Semantic mode, rebuilt on the same section extraction as the structural
    strategy - so it gets the exact same heading/TOC-noise filtering for
    free, and a card can further break on a topic shift *within* a section
    but can never cross a section boundary (a heading change always forces
    a new card, same guarantee as the structural strategy). Requires the
    whole document's sentences in memory to batch-embed them, unlike the
    structural strategy's page-by-page streaming - a real, documented
    tradeoff for the better topic-aware splitting.

    on_progress(done_batches, total_batches), if given, is called after each
    embedding batch.
    """
    # Group consecutive paragraphs sharing a heading into one section text.
    grouped: list[tuple] = []
    cur_heading = object()  # sentinel that won't equal any real heading/None
    cur_parts: list[str] = []
    for para in extract_sections(pages_blocks):
        if para["heading"] != cur_heading:
            if cur_parts:
                grouped.append((cur_heading, cur_parts))
            cur_heading, cur_parts = para["heading"], [para["text"]]
        else:
            cur_parts.append(para["text"])
    if cur_parts:
        grouped.append((cur_heading, cur_parts))

    all_sentences: list[str] = []
    sentence_section: list[int] = []
    for section_idx, (_heading, parts) in enumerate(grouped):
        for sentence in split_sentences(' '.join(parts)):
            all_sentences.append(sentence)
            sentence_section.append(section_idx)

    if not all_sentences:
        return []
    if len(all_sentences) == 1:
        return [_finalize(all_sentences, 0, grouped[0][0])]

    model = _get_embedder()
    batch_size = 32
    total_batches = max(1, (len(all_sentences) + batch_size - 1) // batch_size)
    embeddings = []
    for i in range(0, len(all_sentences), batch_size):
        batch = all_sentences[i:i + batch_size]
        embeddings.extend(model.encode(batch, normalize_embeddings=True, show_progress_bar=False))
        if on_progress:
            on_progress(i // batch_size + 1, total_batches)

    chunks = []
    current = [all_sentences[0]]
    current_words = len(all_sentences[0].split())
    current_section = sentence_section[0]

    for i in range(1, len(all_sentences)):
        section_changed = sentence_section[i] != current_section
        sentence_words = len(all_sentences[i].split())
        similarity = 0.0 if section_changed else float(embeddings[i - 1] @ embeddings[i])
        topic_shift = similarity < similarity_threshold
        would_overflow = current_words + sentence_words > max_words

        if section_changed or (topic_shift and current_words >= MIN_WORDS_PER_CHUNK) or would_overflow:
            chunks.append(_finalize(current, len(chunks), grouped[current_section][0]))
            current, current_words = [], 0
            current_section = sentence_section[i]

        current.append(all_sentences[i])
        current_words += sentence_words

    if current:
        chunks.append(_finalize(current, len(chunks), grouped[current_section][0]))

    return chunks


# ---------------------------------------------------------------------------
# Paragraph-and-heading-aware chunking for PDFs (the "fixed" strategy).
#
# The old version packed sentences into fixed word-count windows blindly,
# which meant a paragraph could get sliced in half right at the window
# boundary, and a lone chapter heading ("Chapter 3") could end up as its
# own nearly-empty card. This version works off the PDF's own paragraph
# structure instead (each page's text "blocks", as PyMuPDF's layout
# analysis already detects them - this is document structure, not a
# machine learning model):
#   - short, unpunctuated blocks are treated as headings and folded into
#     the paragraph that follows them, instead of becoming a standalone card
#   - table-of-contents-style noise ("Chapter 3 ..... 45") is dropped
#   - a full paragraph is always kept together in one card; a card only
#     ever combines *whole* paragraphs, so nothing from paragraph A ever
#     spills into the same card as a fragment of paragraph B's neighbor
#   - the one exception is a single paragraph that's already longer than
#     the target card size - that gets split by sentence internally, but
#     still never blended with a different paragraph's text
#
# Headings are no longer glued into card text at all (the old version did
# "Chapter 3 Right of Way Rules. Drivers approaching..." as one sentence,
# which read like clutter, not organization). Instead each card carries a
# `heading` field naming the section it belongs to - the frontend uses
# that to render section dividers with cards grouped underneath, closer to
# how Anki/SaveMyExams organize a deck than a flat, undifferentiated list.
# A card's section never changes mid-card: hitting a new heading always
# closes out whatever card was being built, which is also what guarantees
# a heading can never end up alone as its own flashcard.
# ---------------------------------------------------------------------------

_TOC_NOISE = re.compile(r'\.{4,}\s*\d+\s*$')  # "Chapter 3 ..... 45"

# Explicit chapter/section markers - these are ALWAYS headings regardless
# of trailing punctuation. This closes a real gap: "Chapter 3." (with a
# period) used to fall through to the generic heuristic below, which
# excludes anything ending in '.!?' on the assumption that's a real
# sentence - so a heading that happened to end in a period was slipping
# through and becoming its own flashcard. Matching the pattern explicitly
# means punctuation can no longer hide a heading from detection.
_EXPLICIT_HEADING = re.compile(
    r'^(chapter|section|part|unit|module|lesson|ch\.?|sec\.?)\s*\d*[a-z]?\.?\s*:?\s*$',
    re.IGNORECASE,
)


def _is_toc_noise(text: str) -> bool:
    if _TOC_NOISE.search(text):
        return True
    if re.fullmatch(r'\d+', text.strip()):  # a lone page number
        return True
    return False


def _is_heading_like(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    if _EXPLICIT_HEADING.match(t):
        return True
    words = t.split()
    if len(words) > 8:
        return False
    if t[-1] in '.!?':  # ends like a normal sentence, not a heading
        return False
    return True


def extract_sections(pages_blocks):
    """
    Consume an iterator that yields, per page, a list of that page's text
    blocks in reading order. Yields {"heading": str|None, "text": str} for
    every real paragraph, in document order - heading blocks are folded
    into `heading` rather than yielded as their own entry, and
    table-of-contents noise is dropped entirely. Consecutive heading-like
    blocks (e.g. a "Chapter 3" line immediately followed by a "Right of Way
    Rules" title line) are merged into a single combined heading rather
    than the second one silently overwriting the first.
    """
    current_heading = None
    pending_heading_parts: list[str] = []

    for page_blocks in pages_blocks:
        for raw in page_blocks:
            text = re.sub(r'\s+', ' ', raw).strip()
            if not text or _is_toc_noise(text):
                continue
            if _is_heading_like(text):
                pending_heading_parts.append(text)
                continue
            if pending_heading_parts:
                current_heading = ' — '.join(p.rstrip('.') for p in pending_heading_parts)
                pending_heading_parts = []
            yield {"heading": current_heading, "text": text}


def chunk_sections(pages_blocks, words_per_chunk: int = DEFAULT_WORDS_PER_CHUNK):
    """
    The structural (non-ML) chunking strategy: packs whole paragraphs into
    word-limited cards, never crossing a section (heading) boundary and
    never splitting one paragraph across two cards. Streams page-by-page,
    so memory stays bounded to a handful of paragraphs regardless of
    document length.
    """
    buffer: list[str] = []
    buffer_words = 0
    buffer_heading = None
    index = 0

    for para in extract_sections(pages_blocks):
        heading, text = para["heading"], para["text"]
        words = len(text.split())

        if buffer and heading != buffer_heading:
            yield _finalize(buffer, index, buffer_heading)
            index += 1
            buffer, buffer_words = [], 0

        if words > words_per_chunk:
            if buffer:
                yield _finalize(buffer, index, buffer_heading)
                index += 1
                buffer, buffer_words = [], 0
            for sub in chunk_text(text, words_per_chunk):
                sub["heading"] = heading
                sub["index"] = index
                index += 1
                yield sub
            buffer_heading = heading
            continue

        if buffer and buffer_words + words > words_per_chunk:
            yield _finalize(buffer, index, buffer_heading)
            index += 1
            buffer, buffer_words = [], 0

        buffer.append(text)
        buffer_words += words
        buffer_heading = heading

    if buffer:
        yield _finalize(buffer, index, buffer_heading)


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
