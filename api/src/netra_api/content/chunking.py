"""Deterministic, structure-aware search-chunk construction.

Reading blocks remain the navigation source of truth.  Chunks only group
those blocks for retrieval and retain every contributing block ID.

``ChunkBlock.parent_id`` and ``parent_path`` are supplied by an upstream
structural parser/adapter.  This module does not infer sections from text.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid5

from tokenizers import Regex, Tokenizer, models, pre_tokenizers

from netra_api.content.reading.blocks import BlockType, ReadingBlock
from netra_api.content.retrieval.chunks import SearchChunk


class LocalTokenCounter:
    """Deterministic LOCAL CHUNK-BUDGET TOKENIZATION.

    This is intentionally a local WordLevel tokenizer: it provides stable
    token budgets and is not claimed to reproduce Gemini's internal tokenizer.
    Offsets are retained so overlap is copied from the original text rather
    than reconstructed from token IDs.
    """

    def __init__(self) -> None:
        self._tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
        self._tokenizer.pre_tokenizer = pre_tokenizers.Split(Regex(r"\w+|[^\w\s]"), "isolated")

    def count(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=False).ids)

    def tail(self, text: str, token_count: int) -> str:
        encoding = self._tokenizer.encode(text, add_special_tokens=False)
        if not encoding.ids or token_count <= 0:
            return ""
        start = max(0, len(encoding.ids) - token_count)
        return text[encoding.offsets[start][0] :].lstrip()

    def take(self, text: str, start: int, count: int) -> str:
        encoding = self._tokenizer.encode(text, add_special_tokens=False)
        if start >= len(encoding.ids) or count <= 0:
            return ""
        end = min(len(encoding.ids), start + count)
        return text[encoding.offsets[start][0] : encoding.offsets[end - 1][1]]


@dataclass(frozen=True)
class ChunkBlock:
    block: ReadingBlock
    parent_id: str = "root"
    parent_path: tuple[str, ...] = ()

    @classmethod
    def from_reading_block(cls, block: ReadingBlock) -> "ChunkBlock":
        location = block.structured_location or {}
        return cls(block, str(location.get("parent_id", "document")),
                   tuple(str(value) for value in location.get("parent_path", [])))

    @property
    def text(self) -> str:
        return " ".join(sentence.text.strip() for sentence in self.block.sentences if sentence.text.strip())


@dataclass(frozen=True)
class _Atom:
    text: str
    block_ids: tuple[UUID, ...]


_STRUCTURED = {
    BlockType.TABLE,
    BlockType.FIGURE_REFERENCE,
    BlockType.EQUATION_REFERENCE,
    BlockType.CODE,
    BlockType.LIST_ITEM,
}


class StructureAwareChunker:
    def __init__(self, *, target_tokens: int = 500, min_tokens: int = 350,
                 max_tokens: int = 700, overlap_tokens: int = 64,
                 token_counter: LocalTokenCounter | None = None) -> None:
        if not 0 < min_tokens <= target_tokens <= max_tokens:
            raise ValueError("chunk token bounds are invalid")
        if not 0 <= overlap_tokens < max_tokens:
            raise ValueError("chunk overlap is invalid")
        self.target_tokens = target_tokens
        self.min_tokens = min_tokens
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.tokens = token_counter or LocalTokenCounter()

    def chunk(self, blocks: list[ChunkBlock]) -> list[SearchChunk]:
        if not blocks:
            return []
        source_version_ids = {item.block.source_version_id for item in blocks}
        if len(source_version_ids) != 1:
            raise ValueError("all chunk blocks must belong to one source version")
        if len({item.block.block_id for item in blocks}) != len(blocks):
            raise ValueError("chunk blocks must have unique IDs")
        if len({item.block.ordinal for item in blocks}) != len(blocks):
            raise ValueError("chunk blocks must have unique ordinals")

        output: list[SearchChunk] = []
        group: list[ChunkBlock] = []
        group_key: tuple[str, tuple[str, ...]] | None = None

        def flush() -> None:
            nonlocal group
            if group:
                output.extend(self._chunk_parent(group))
                group = []

        for item in sorted(blocks, key=lambda value: value.block.ordinal):
            key = (item.parent_id, item.parent_path)
            if group_key is not None and key != group_key:
                flush()
            group_key = key
            group.append(item)
        flush()
        return output

    def _chunk_parent(self, blocks: list[ChunkBlock]) -> list[SearchChunk]:
        chunks: list[SearchChunk] = []
        prose: list[ChunkBlock] = []

        def flush_prose() -> None:
            nonlocal prose
            if prose:
                chunks.extend(self._prose_chunks(prose))
                prose = []

        for index, item in enumerate(blocks):
            if item.block.block_type in _STRUCTURED:
                flush_prose()
                chunks.append(self._make_chunk(item.block.source_version_id, item.text, (item.block.block_id,), item, parent_index=index))
            else:
                prose.append(item)
        flush_prose()
        return chunks

    def _prose_chunks(self, blocks: list[ChunkBlock]) -> list[SearchChunk]:
        atoms: list[_Atom] = []
        for item in blocks:
            text = item.text
            if not text:
                continue
            # Every prose body must leave room for the next chunk's overlap.
            # This is true even for the first body because an otherwise-large
            # atom can become a subsequent body after packing.
            if self.tokens.count(text) <= self.max_tokens - self.overlap_tokens:
                atoms.append(_Atom(text, (item.block.block_id,)))
                continue
            sentences = [sentence.text.strip() for sentence in item.block.sentences if sentence.text.strip()]
            if not sentences:
                sentences = [text]
            for sentence in sentences:
                atoms.extend(self._split_oversized(sentence, item.block.block_id))

        groups: list[list[_Atom]] = []
        current: list[_Atom] = []
        current_tokens = 0
        for atom in atoms:
            atom_tokens = self.tokens.count(atom.text)
            budget = self.target_tokens if not groups else self.target_tokens - self.overlap_tokens
            if current and (current_tokens + atom_tokens > budget or current_tokens + atom_tokens > self.max_tokens - self.overlap_tokens):
                groups.append(current)
                current = []
                current_tokens = 0
                budget = self.target_tokens - self.overlap_tokens
            current.append(atom)
            current_tokens += atom_tokens
        if current:
            groups.append(current)
        if len(groups) > 1 and self.tokens.count(self._join(groups[-1])) < self.min_tokens:
            merged_tokens = self.tokens.count(self._join(groups[-2])) + self.tokens.count(self._join(groups[-1]))
            if merged_tokens <= self.max_tokens - self.overlap_tokens:
                groups[-2].extend(groups.pop())

        result: list[SearchChunk] = []
        previous_text = ""
        previous_overlap_ids: tuple[UUID, ...] = ()
        version_id = blocks[0].block.source_version_id
        for index, group in enumerate(groups):
            body = self._join(group)
            prefix = self.tokens.tail(previous_text, self.overlap_tokens) if index and self.overlap_tokens else ""
            text = f"{prefix} {body}".strip() if prefix else body
            current_ids = tuple(id_ for atom in group for id_ in atom.block_ids)
            ids = tuple(dict.fromkeys(previous_overlap_ids + current_ids)) if prefix else tuple(dict.fromkeys(current_ids))
            result.append(self._make_chunk(version_id, text, ids, blocks[0], parent_index=index))
            previous_text = body
            previous_overlap_ids = self._suffix_block_ids(group)
        return result

    def _suffix_block_ids(self, atoms: list[_Atom]) -> tuple[UUID, ...]:
        """Return exactly the blocks touched by the next overlap suffix."""
        remaining = self.overlap_tokens
        suffix: list[UUID] = []
        for atom in reversed(atoms):
            suffix[0:0] = list(atom.block_ids)
            remaining -= self.tokens.count(atom.text)
            if remaining <= 0:
                break
        return tuple(dict.fromkeys(suffix))

    def _split_oversized(self, text: str, block_id: UUID) -> list[_Atom]:
        body_limit = self.max_tokens - self.overlap_tokens
        if self.tokens.count(text) <= body_limit:
            return [_Atom(text, (block_id,))]
        result: list[_Atom] = []
        start = 0
        total = self.tokens.count(text)
        while start < total:
            piece = self.tokens.take(text, start, body_limit)
            if not piece:
                break
            result.append(_Atom(piece, (block_id,)))
            start += body_limit
        return result

    def _join(self, atoms: list[_Atom]) -> str:
        return " ".join(atom.text for atom in atoms).strip()

    def _make_chunk(self, version_id: UUID, text: str, block_ids: tuple[UUID, ...], source: ChunkBlock, *, parent_index: int = 0) -> SearchChunk:
        parent = source.parent_id
        chunk_id = uuid5(version_id, f"chunk:{parent}:{parent_index}:{text}")
        return SearchChunk(
            chunk_id=chunk_id,
            source_version_id=version_id,
            text=text,
            block_ids=list(block_ids),
            embedding_version="pending",
            metadata={"parent_id": parent, "parent_path": list(source.parent_path), "token_count": self.tokens.count(text)},
        )
