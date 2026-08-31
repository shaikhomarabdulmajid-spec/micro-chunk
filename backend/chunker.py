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


def _split_oversized_sentence(sentence: str, words_per_chunk: int) -> list[str]:
    """
    A hard ceiling for the one case sentence-splitting can't help with: a
    "sentence" that's actually a giant punctuation-free blob. Code-heavy
    material (a programming guide, a page of code with sparse comments) can
    easily run for hundreds of words with no '.', '!', or '?' at all, so
    split_sentences() hands back the whole thing as a single "sentence" -
    and without this, that becomes one card that runs off the bottom of
    the screen with nothing to stop it. Only kicks in when a sentence is
    well past the target size; ordinary prose is never touched by this.
    """
    words = sentence.split()
    if len(words) <= words_per_chunk * 1.5:
        return [sentence]
    return [' '.join(words[i:i + words_per_chunk]) for i in range(0, len(words), words_per_chunk)]


def chunk_text(text: str, words_per_chunk: int = DEFAULT_WORDS_PER_CHUNK) -> list[dict]:
    """Greedily pack whole sentences into fixed-size (word-count) windows."""
    sentences = []
    for s in split_sentences(text):
        sentences.extend(_split_oversized_sentence(s, words_per_chunk))
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
# Works off the PDF's own structure instead of guessing from text shape:
#   - a line printed in the document's largest heading-sized font becomes
#     a section heading; everything else, however short or table-like,
#     stays body text (see the heading-detection block above)
#   - whole contents/index pages are dropped entirely
#   - a full paragraph is always kept together in one card; a card only
#     ever combines *whole* paragraphs, so nothing from paragraph A ever
#     spills into the same card as a fragment of paragraph B's neighbor
#   - the one exception is a single paragraph that's already longer than
#     the target card size - that gets split by sentence internally, but
#     still never blended with a different paragraph's text
#
# Each card carries a `heading` field naming the section it belongs to -
# the frontend uses that to render section dividers with cards grouped
# underneath. A card's section never changes mid-card: hitting a new
# heading always closes out whatever card was being built, which is also
# what guarantees a heading can never end up alone as its own flashcard.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Heading detection - font-size based, not shape based.
#
# The old version guessed "heading" from what a line LOOKED like: short,
# no trailing punctuation. That collides badly with real documents full of
# short, punctuation-free lines that AREN'T headings - a demerit-points
# table row ("Illegal parking .... 1") has exactly that shape. It also had
# no way to tell a true chapter title apart from a minor bolded aside, so
# it over-segmented into a new "section" every few lines, and a heading
# guessed wrong mid-paragraph would fracture one paragraph across two
# cards under two different headings.
#
# This version uses the PDF's own typography instead: PyMuPDF's "dict"
# text extraction reports each line's font size. We find the document's
# body-text size (the size most of the actual paragraph text is set in),
# then find the single LARGEST font-size tier used anywhere above that -
# that tier is treated as headings. A demerit-points row is body-sized, so
# it can no longer be mistaken for a heading just because it's short. Any
# smaller-but-still-bold sub-heading tier gets folded into the surrounding
# paragraph rather than forcing a new card group - this is also what makes
# groupings broad (dozens of cards under one heading) instead of
# fragmenting on every visually distinct line.
# ---------------------------------------------------------------------------

_TOC_NOISE = re.compile(r'(\.{2,}|-{2,}|_{2,})\s*\d{1,4}\s*$')  # "Chapter 3 ..... 45" / "Chapter 3 ---- 45"


def _looks_like_toc_line(line: str) -> bool:
    t = re.sub(r'\s+', ' ', line).strip()
    if not t or len(t.split()) > 14:
        return False
    if t[-1] in '.!?"\u201d\u2019\')':
        return False
    if _TOC_NOISE.search(t):
        return True
    return bool(re.search(r'(?<![.\d])\d{1,4}$', t))


def _is_toc_page(lines: list) -> bool:
    """
    A whole PAGE is a contents/index listing if most of its lines read like
    contents rows - dropped entirely rather than becoming cards. Scoped to
    the whole page (not individual lines) deliberately: a demerit-points
    table row has the exact same "<description> <number>" shape as a
    contents row, so checking stray lines anywhere in the document would
    silently delete real table content. A real contents page, by contrast,
    is mostly nothing BUT that shape, page after page - that's the signal.
    """
    texts = [l["text"].strip() for l in lines if l["text"].strip()]
    if len(texts) < 3:
        return False
    toc_like = sum(1 for t in texts if _looks_like_toc_line(t))
    return toc_like / len(texts) >= 0.5


def _compute_body_size(pages: list) -> float:
    """
    The font size most of the document's actual prose is set in, found by
    weighting each distinct size by how many characters appear at that
    size (so one page of a big pull-quote can't outvote 300 pages of body
    text). Short lines are excluded from the vote since headings and table
    rows are usually short and would otherwise skew it.
    """
    votes: dict = {}
    for lines in pages:
        for line in lines:
            t = line["text"].strip()
            if len(t.split()) < 6:
                continue
            size = round(line["size"], 1)
            votes[size] = votes.get(size, 0) + len(t)
    if not votes:
        return 11.0
    return max(votes, key=votes.get)


def _compute_heading_threshold(pages: list, body_size: float):
    """
    The single largest font-size tier found above body size, or None if
    the document has no real size variation to work with. Deliberately
    picks only the TOP tier (not every size bigger than body text) so a
    document with both chapter titles and smaller bolded sub-headings
    treats only the chapter titles as section breaks - broader groupings,
    on purpose.
    """
    tiers = set()
    for lines in pages:
        for line in lines:
            size = round(line["size"], 1)
            if size > body_size * 1.12:
                tiers.add(size)
    if not tiers:
        return None
    return max(tiers) - 0.05


def _is_heading_line(line: dict, body_size: float, heading_threshold) -> bool:
    """
    A line counts as a heading if EITHER signal fires:
      - it's in the document's top font-size tier (the primary signal,
        strongest when a document actually varies its heading size), or
      - it's bold, no smaller than body text, and short - this is what
        catches documents (a lot of technical/code-heavy guides included)
        that style headings as bold-but-same-size rather than bigger.
    Without the second branch, a document with zero size variation
    between headings and body falls back to ZERO headings found -
    everything lands in one giant undivided section, which is exactly the
    "grouped ALL flashcards under ONE heading" failure. Table rows and
    demerit-point-style content are excluded from this safely: that kind
    of tabular content is essentially never bold in real documents, only
    the row label conventions (short, ends in a number) that used to
    trip up the old shape-only detector.
    """
    t = line["text"].strip()
    if not t or len(t.split()) > 20:  # a giant blob at heading size/weight is a pull-quote, not a title
        return False
    size = round(line["size"], 1)
    if heading_threshold is not None and size >= heading_threshold:
        return True
    return bool(line.get("bold")) and size >= body_size - 0.1 and len(t.split()) <= 12


def extract_sections(pages_lines):
    """
    Consume an iterable that yields, per page, a list of that page's text
    lines in reading order as {"text": str, "size": float, "bold": bool}
    dicts (see main.py's page_lines_iter). Yields {"heading": str|None,
    "text": str} for every real paragraph, in document order. Consecutive
    heading-sized lines (e.g. a "DEMERIT POINTS" line immediately followed
    by a "For Moving Violations" subtitle line, both in the same large
    size) are merged into one combined heading rather than the second
    silently overwriting the first.

    Needs the whole document's lines up front, not just one page at a
    time, to work out what "body text size" and "heading size" even mean
    for THIS specific PDF - a deliberate tradeoff, and a real one: this is
    no longer a page-by-page streaming pass. But font metadata for even a
    900-page book is a few MB at most, nothing like the cost of holding
    page images, so it's a fine trade for actually getting groupings right.
    """
    pages = [p for p in pages_lines if not _is_toc_page(p)]
    body_size = _compute_body_size(pages)
    heading_threshold = _compute_heading_threshold(pages, body_size)

    current_heading = None
    pending_heading_parts: list[str] = []
    para_buf: list[str] = []

    def close_heading():
        nonlocal pending_heading_parts, current_heading
        if pending_heading_parts:
            current_heading = ' — '.join(p.rstrip('.') for p in pending_heading_parts)
            pending_heading_parts = []

    for page in pages:
        for line in page:
            t = line["text"].strip()
            if not t:
                continue
            if _is_heading_line(line, body_size, heading_threshold):
                if para_buf:
                    text = ' '.join(para_buf).strip()
                    para_buf = []
                    if text:
                        yield {"heading": current_heading, "text": text}
                pending_heading_parts.append(t)
                continue
            close_heading()
            para_buf.append(t)
    close_heading()
    if para_buf:
        text = ' '.join(para_buf).strip()
        if text:
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
