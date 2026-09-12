from uuid import uuid4

import pytest
from pydantic import ValidationError

from netra_api.content.reading.blocks import BlockType, ReadingBlock, Sentence


def test_reading_block_defaults_to_empty_sentence_list():
    block = ReadingBlock(
        block_id=uuid4(), source_version_id=uuid4(), ordinal=0, block_type=BlockType.PARAGRAPH
    )
    assert block.sentences == []
    assert block.locator is None


def test_reading_block_holds_ordered_sentences():
    sentence = Sentence(sentence_id=uuid4(), ordinal=0, text="Cells are the basic unit of life.")
    block = ReadingBlock(
        block_id=uuid4(),
        source_version_id=uuid4(),
        ordinal=0,
        block_type=BlockType.PARAGRAPH,
        sentences=[sentence],
    )
    assert block.sentences[0].text == "Cells are the basic unit of life."


def test_negative_ordinal_rejected():
    with pytest.raises(ValidationError):
        ReadingBlock(
            block_id=uuid4(), source_version_id=uuid4(), ordinal=-1, block_type=BlockType.PARAGRAPH
        )
