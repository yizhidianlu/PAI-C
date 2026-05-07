"""Paper markdown chunking + on-disk chunk index (P0 #1).

Splits each paper's markdown body into ~400-token chunks (heading-aware,
sentence-fallback, with mild overlap) so retrieval can return *passages*
instead of paper titles. Compose paragraph-mode then injects 2-3 chunks
per cited paper into the LLM prompt — the model finally sees the actual
content of the papers it's citing instead of just a one-line summary.

Layout on disk:
- ``<project>/.paic/library/chunks/<cite_key>.json`` — one file per paper.
  Schema: ``{"cite_key": str, "chunks": [Chunk.model_dump(...), ...]}``.
- The directory is created on first write; older projects without it
  fall back gracefully (callers treat "no chunk file" as "no chunks").

Token counting: whitespace-split word count is used as a cheap stable
approximation. We don't ship tiktoken here because (a) BM25 doesn't care
about tokenizer choice and (b) the exact target_tokens cap is a
soft-target — a 350-token chunk is fine, a 600-token chunk is too big.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from paic.workspace.paths import ProjectPaths


# Heading regex matches ``# H1``, ``## H2``, ``### H3``. Levels 4+ are
# treated as plain text — papers rarely use them, and treating them as
# heading boundaries fragments the chunk graph.
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)
_PARAGRAPH_SPLIT_RE = re.compile(r"\n{2,}")
# Conservative sentence splitter: period / question / exclamation followed
# by whitespace + uppercase or end of input. Avoids breaking on "e.g." or
# "Fig. 3" — we want a few oversized sentences over a chunk that splits
# mid-clause.
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")


class Chunk(BaseModel):
    """One contiguous passage from a paper's markdown body."""

    chunk_id: str
    """Stable id of the form ``<cite_key>__c<NNN>``. Globally unique
    across the project library."""

    paper_cite_key: str
    """The paper this chunk came from. Joins back to ``selected.yaml``."""

    section_path: str = ""
    """Heading hierarchy as ``"H1 > H2 > H3"``. Empty string when the
    chunk falls before any heading or the markdown has no headings."""

    text: str
    token_count: int
    char_offset_start: int
    """Offset into the original markdown body — for re-extraction /
    debugging. Not used by retrieval."""

    char_offset_end: int


class ChunkIndex(BaseModel):
    """On-disk shape for ``library/chunks/<cite_key>.json``."""

    cite_key: str
    chunks: list[Chunk] = Field(default_factory=list)
    schema_version: int = 1


# ---------------------------------------------------------- helpers


def _count_tokens(text: str) -> int:
    """Whitespace word count. Cheap, stable, good enough for size targets."""
    return len(text.split())


def _split_by_headings(md_text: str) -> list[tuple[str, int, str]]:
    """Walk the markdown emitting ``(section_path, char_offset_start, body_text)``
    blocks split by ``# / ## / ###`` headings.

    A "section_path" tracks the current heading stack as ``H1 > H2 > H3``.
    Text appearing before the first heading is emitted with an empty
    section_path. Headings themselves are stripped from the emitted body
    so that chunk text reads cleanly.
    """
    matches = list(_HEADING_RE.finditer(md_text))
    if not matches:
        return [("", 0, md_text.strip())] if md_text.strip() else []

    blocks: list[tuple[str, int, str]] = []
    # Pre-heading prologue, if any
    if matches[0].start() > 0:
        prologue = md_text[: matches[0].start()].strip()
        if prologue:
            blocks.append(("", 0, prologue))

    stack: list[tuple[int, str]] = []
    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        # Pop stack to current level - 1.
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        section_path = " > ".join(t for _, t in stack)

        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        body = md_text[body_start:body_end].strip()
        if body:
            blocks.append((section_path, body_start, body))
    return blocks


def _greedy_pack(
    text: str,
    *,
    target_tokens: int,
    overlap_tokens: int,
) -> list[tuple[str, int]]:
    """Pack a long block into target-sized chunks with mild overlap.

    Splits on paragraph boundaries first (``\\n\\n``). When a single paragraph
    is itself larger than ``target_tokens``, falls back to sentence splits.
    Returns ``[(chunk_text, char_offset_within_input), ...]``.

    Overlap: the last ``overlap_tokens`` words of chunk N are prepended to
    chunk N+1 — keeps cross-paragraph context for retrieval queries that
    span a transition.
    """
    if not text.strip():
        return []
    if _count_tokens(text) <= target_tokens:
        return [(text.strip(), 0)]

    pieces: list[tuple[str, int]] = []
    cursor = 0
    for paragraph in _PARAGRAPH_SPLIT_RE.split(text):
        if not paragraph.strip():
            cursor = text.find(paragraph, cursor) + len(paragraph)
            continue
        offset = text.find(paragraph, cursor)
        if offset < 0:
            offset = cursor
        cursor = offset + len(paragraph)
        if _count_tokens(paragraph) <= target_tokens:
            pieces.append((paragraph.strip(), offset))
        else:
            sentence_offset = offset
            for sentence in _SENTENCE_END_RE.split(paragraph):
                sentence = sentence.strip()
                if not sentence:
                    continue
                if _count_tokens(sentence) <= target_tokens:
                    pieces.append((sentence, sentence_offset))
                else:
                    # Last resort: a single "sentence" longer than target
                    # (no periods, code blocks, latex glob) — hard-split by
                    # words so the chunker doesn't silently emit oversized
                    # passages.
                    words = sentence.split()
                    for i in range(0, len(words), target_tokens):
                        sub = " ".join(words[i:i + target_tokens])
                        pieces.append((sub, sentence_offset))
                sentence_offset += len(sentence) + 1

    out: list[tuple[str, int]] = []
    buf_words: list[str] = []
    buf_offset: int | None = None
    for piece_text, piece_offset in pieces:
        piece_words = piece_text.split()
        if buf_offset is None:
            buf_offset = piece_offset
        if buf_words and len(buf_words) + len(piece_words) > target_tokens:
            out.append((" ".join(buf_words), buf_offset))
            tail = buf_words[-overlap_tokens:] if overlap_tokens > 0 else []
            buf_words = list(tail) + piece_words
            buf_offset = piece_offset
        else:
            buf_words.extend(piece_words)
    if buf_words and buf_offset is not None:
        out.append((" ".join(buf_words), buf_offset))
    return out


# ---------------------------------------------------------- public api


def chunk_paper_markdown(
    md_text: str,
    paper_cite_key: str,
    *,
    target_tokens: int = 400,
    overlap_tokens: int = 80,
) -> list[Chunk]:
    """Split ``md_text`` (one paper's markdown body) into ``Chunk`` records.

    Strategy:
    1. Split by markdown ``#``/``##``/``###`` headings to keep section
       boundaries intact.
    2. For each section block, if it fits within ``target_tokens``, emit
       as one chunk; otherwise greedy-pack paragraphs (with sentence
       fallback) into ``target_tokens``-sized chunks with
       ``overlap_tokens`` of trailing word overlap.

    Returns an empty list for empty / whitespace-only input. Stable
    chunk_ids of the form ``<cite_key>__c<NNN>``.
    """
    if not md_text or not md_text.strip():
        return []
    if not paper_cite_key:
        raise ValueError("paper_cite_key required")

    blocks = _split_by_headings(md_text)
    if not blocks:
        return []

    chunks: list[Chunk] = []
    idx = 0
    for section_path, block_start, block_text in blocks:
        block_tokens = _count_tokens(block_text)
        if block_tokens <= target_tokens:
            sub_chunks = [(block_text, 0)]
        else:
            sub_chunks = _greedy_pack(
                block_text,
                target_tokens=target_tokens,
                overlap_tokens=overlap_tokens,
            )
        for sub_text, sub_offset in sub_chunks:
            sub_text = sub_text.strip()
            if not sub_text:
                continue
            chunks.append(Chunk(
                chunk_id=f"{paper_cite_key}__c{idx:03d}",
                paper_cite_key=paper_cite_key,
                section_path=section_path,
                text=sub_text,
                token_count=_count_tokens(sub_text),
                char_offset_start=block_start + sub_offset,
                char_offset_end=block_start + sub_offset + len(sub_text),
            ))
            idx += 1
    return chunks


# ---------------------------------------------------------- on-disk index


def _chunks_dir(paths: ProjectPaths) -> Path:
    return paths.library_dir / "chunks"


def chunk_index_path(paths: ProjectPaths, cite_key: str) -> Path:
    return _chunks_dir(paths) / f"{cite_key}.json"


def save_chunk_index(paths: ProjectPaths, cite_key: str, chunks: list[Chunk]) -> Path:
    """Write ``library/chunks/<cite_key>.json``. Creates the directory.

    Returns the resulting path so callers can surface it. Overwrites any
    pre-existing index for the same cite_key (chunking is deterministic
    given identical input markdown).
    """
    target_dir = _chunks_dir(paths)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{cite_key}.json"
    payload = ChunkIndex(cite_key=cite_key, chunks=chunks).model_dump(mode="json")
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_chunk_index(paths: ProjectPaths, cite_key: str) -> list[Chunk]:
    """Read the per-paper chunk index. Returns ``[]`` when absent or invalid."""
    path = chunk_index_path(paths, cite_key)
    if not path.is_file():
        return []
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, dict):
        return []
    try:
        index = ChunkIndex.model_validate(raw)
    except Exception:  # noqa: BLE001 — corrupt cache is recoverable, just return empty
        return []
    return list(index.chunks)


def load_all_chunks(paths: ProjectPaths) -> dict[str, list[Chunk]]:
    """Map cite_key → chunks for every chunk index on disk.

    Used by retrieval to build the cross-paper chunk corpus. Empty dict
    when the chunks directory doesn't exist (back-compat: the project was
    created before P0 #1 shipped).
    """
    out: dict[str, list[Chunk]] = {}
    target_dir = _chunks_dir(paths)
    if not target_dir.is_dir():
        return out
    for path in target_dir.iterdir():
        if not (path.is_file() and path.suffix == ".json"):
            continue
        cite_key = path.stem
        chunks = load_chunk_index(paths, cite_key)
        if chunks:
            out[cite_key] = chunks
    return out


def chunk_index_build(
    paths: ProjectPaths,
    cite_key: str,
    md_text: str,
    *,
    target_tokens: int = 400,
    overlap_tokens: int = 80,
) -> list[Chunk]:
    """One-call helper: chunk + persist. Returns the chunks for callers
    that want to introspect (e.g. a tool wrapper that returns counts).

    No-op (returns ``[]``) on empty markdown — keeps the auto-chunk hook
    in summarize harmless for projects with no body text.
    """
    if not md_text or not md_text.strip():
        return []
    chunks = chunk_paper_markdown(
        md_text,
        cite_key,
        target_tokens=target_tokens,
        overlap_tokens=overlap_tokens,
    )
    if chunks:
        save_chunk_index(paths, cite_key, chunks)
    return chunks
