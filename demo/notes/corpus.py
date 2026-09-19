"""The notes the agent answers from, and how they get into the run database.

The corpus is three short hand-written study notes in demo/notes/corpus/. It is
deliberately small and checkable: every number in it can be verified by hand,
and it deliberately does NOT cover some things (parallel circuits, transistors),
so that "the notes do not cover this" is a case we can test rather than a hope.

This module only reads files and calls the kit's own retrieval
(slice/retrieve.py). Nothing here embeds or searches by itself. The kit's
retrieval imports sqlite-vec and fastembed, so those imports are lazy: reading
the notes works everywhere, and ingesting or searching needs the kit's
environment (the Codespace image has it).
"""
from __future__ import annotations

from pathlib import Path

NOTES_DIR = Path(__file__).parent / "corpus"


def note_files() -> list[Path]:
    """The corpus files, in a stable order."""
    return sorted(NOTES_DIR.glob("*.md"))


def note_texts() -> dict[str, str]:
    """{file name: full text}. What a human would read to check an answer."""
    return {f.name: f.read_text(encoding="utf-8") for f in note_files()}


def ingest_notes(store) -> dict:
    """Chunk and embed the notes into the run database. Idempotent: the kit's
    chunk ids are content hashes, so calling this twice adds nothing new."""
    from slice.retrieve import ingest
    return ingest(store, NOTES_DIR)


def search_notes(store, query: str, k: int = 3, allowed: set[str] | None = None):
    """The k best passages for the query, each with the file and position it came from.
    Provenance is the point: a citation is a chunk that was really returned.

    Hybrid, not embeddings alone: the kit's small embedding model ranked the right note
    fifth of five for one of our questions (see demo/notes/retrieval.py)."""
    from .retrieval import hybrid_search
    return hybrid_search(store, query, k=k, allowed=allowed)
