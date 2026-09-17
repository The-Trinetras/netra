"""Convert parser output into stable, navigable reading blocks.

This is deliberately a provider-neutral boundary.  It does not parse source
bytes or invent Section/Subsection hierarchy: the current ``ParsedBlock``
contract contains only text and a type hint. Parser adapters must provide or
derive structural parent information before true hierarchy-aware chunking can
occur. This module assigns stable IDs and sentence ordinals for an immutable
source version.
"""

from __future__ import annotations

import json
import re
from uuid import NAMESPACE_URL, UUID, uuid5

from netra_api.content.providers.llamaparse import ParsedBlock
from netra_api.content.reading.blocks import BlockType, ReadingBlock, Sentence

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def build_reading_blocks(source_version_id: UUID, parsed_blocks: list[ParsedBlock]) -> list[ReadingBlock]:
    """Build deterministic reading blocks without reordering parser output."""

    result: list[ReadingBlock] = []
    for ordinal, parsed in enumerate(parsed_blocks):
        text = " ".join(parsed.text.split())
        if not text:
            continue
        try:
            block_type = BlockType(parsed.block_type_hint)
        except ValueError as exc:
            raise ValueError(f"unsupported parser block type: {parsed.block_type_hint}") from exc
        block_id = uuid5(source_version_id, f"block:{ordinal}:{block_type.value}:{text}")
        sentences = [part.strip() for part in _SENTENCE_RE.split(text) if part.strip()] or [text]
        result.append(
            ReadingBlock(
                block_id=block_id,
                source_version_id=source_version_id,
                ordinal=len(result),
                block_type=block_type,
                locator=(json.dumps(parsed.structured_location, sort_keys=True, separators=(",", ":"))
                         if parsed.structured_location else None),
                page_index=parsed.page_index,
                printed_page=parsed.printed_page,
                structured_location={
                    **parsed.structured_location,
                    "parent_id": parsed.parent_id,
                    "parent_path": list(parsed.parent_path),
                    "source_metadata": parsed.source_metadata,
                },
                sentences=[
                    Sentence(
                        sentence_id=uuid5(NAMESPACE_URL, f"{block_id}:sentence:{index}:{sentence}"),
                        ordinal=index,
                        text=sentence,
                    )
                    for index, sentence in enumerate(sentences)
                ],
            )
        )
    return result
