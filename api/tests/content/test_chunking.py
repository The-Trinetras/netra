from uuid import uuid4

from netra_api.content.chunking import ChunkBlock, LocalTokenCounter, StructureAwareChunker
from netra_api.content.ingestion.structure import build_reading_blocks
from netra_api.content.providers.llamaparse import ParsedBlock
from netra_api.content.reading.blocks import BlockType, ReadingBlock, Sentence


def _block(version_id, ordinal, text, kind=BlockType.PARAGRAPH):
    return ReadingBlock(
        block_id=uuid4(), source_version_id=version_id, ordinal=ordinal,
        block_type=kind, sentences=[Sentence(sentence_id=uuid4(), ordinal=0, text=text)],
    )


def test_structure_builder_is_deterministic_and_preserves_order():
    version = uuid4()
    parsed = [ParsedBlock(text="Heading", block_type_hint="heading"), ParsedBlock(text="Body.", block_type_hint="paragraph")]
    first = build_reading_blocks(version, parsed)
    second = build_reading_blocks(version, parsed)
    assert [block.block_id for block in first] == [block.block_id for block in second]
    assert [block.ordinal for block in first] == [0, 1]


def test_prose_overlap_is_real_and_structured_content_has_no_overlap():
    version = uuid4()
    blocks = [ChunkBlock(_block(version, i, "word " * 120), parent_id="section") for i in range(5)]
    prose = StructureAwareChunker().chunk(blocks)
    assert len(prose) > 1
    counter = LocalTokenCounter()
    assert counter.tail(prose[0].text, 64) in prose[1].text

    table = ChunkBlock(_block(version, 9, "cell " * 500, BlockType.TABLE), parent_id="section")
    structured = StructureAwareChunker().chunk([table])
    assert structured[0].metadata["token_count"] == counter.count(structured[0].text)
    assert structured[0].text == table.text

    before = ChunkBlock(_block(version, 7, "before " * 120), parent_id="section")
    after = ChunkBlock(_block(version, 11, "after " * 120), parent_id="section")
    separated = StructureAwareChunker().chunk([before, table, after])
    assert separated[1].text == table.text
    assert counter.tail(separated[0].text, 64) not in separated[1].text
    assert counter.tail(separated[1].text, 64) not in separated[2].text


def test_oversized_sentence_accounts_for_overlap_and_never_exceeds_maximum():
    version = uuid4()
    sentence = " ".join(f"term{i}" for i in range(1_401))
    block = ChunkBlock(_block(version, 0, sentence), parent_id="section")
    chunker = StructureAwareChunker()
    chunks = chunker.chunk([block])
    counter = LocalTokenCounter()
    assert len(chunks) > 1
    assert all(counter.count(chunk.text) <= 700 for chunk in chunks)
    assert counter.tail(chunks[0].text, 64) in chunks[1].text


def test_overlap_provenance_includes_only_blocks_touched_by_overlap():
    version = uuid4()
    blocks = [
        ChunkBlock(_block(version, 0, "a " * 210), parent_id="section"),
        ChunkBlock(_block(version, 1, "b " * 15), parent_id="section"),
        ChunkBlock(_block(version, 2, "c " * 10), parent_id="section"),
        ChunkBlock(_block(version, 3, "d " * 10), parent_id="section"),
        ChunkBlock(_block(version, 4, "e " * 100), parent_id="section"),
    ]
    chunks = StructureAwareChunker().chunk(blocks)
    assert len(chunks) == 2
    assert chunks[1].block_ids == [blocks[1].block.block_id, blocks[2].block.block_id,
                                   blocks[3].block.block_id, blocks[4].block.block_id]
    assert LocalTokenCounter().tail(chunks[0].text, 64) in chunks[1].text


def test_parent_boundaries_do_not_overlap_and_provenance_is_retained():
    version = uuid4()
    first = ChunkBlock(_block(version, 0, "alpha " * 100), parent_id="section-a", parent_path=("A",))
    second = ChunkBlock(_block(version, 1, "beta " * 100), parent_id="section-b", parent_path=("B",))
    chunks = StructureAwareChunker().chunk([first, second])
    assert [chunk.metadata["parent_id"] for chunk in chunks] == ["section-a", "section-b"]
    assert chunks[0].block_ids == [first.block.block_id]
    assert chunks[1].block_ids == [second.block.block_id]
