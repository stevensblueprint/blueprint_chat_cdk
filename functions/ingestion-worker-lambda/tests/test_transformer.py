import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from transformer import notion_blocks_to_markdown


def _block(btype: str, text: str, extra: dict = None) -> dict:
    data = {"rich_text": [{"plain_text": text}]}
    if extra:
        data.update(extra)
    return {"type": btype, btype: data}


def test_paragraph():
    blocks = [_block("paragraph", "Hello world")]
    assert notion_blocks_to_markdown(blocks) == "Hello world"


def test_headings():
    blocks = [
        _block("heading_1", "Title"),
        _block("heading_2", "Section"),
        _block("heading_3", "Subsection"),
    ]
    md = notion_blocks_to_markdown(blocks)
    assert "# Title" in md
    assert "## Section" in md
    assert "### Subsection" in md


def test_lists():
    blocks = [
        _block("bulleted_list_item", "item a"),
        _block("numbered_list_item", "item b"),
    ]
    md = notion_blocks_to_markdown(blocks)
    assert "- item a" in md
    assert "1. item b" in md


def test_code_block():
    blocks = [_block("code", "print('hi')", {"language": "python"})]
    md = notion_blocks_to_markdown(blocks)
    assert "```python" in md
    assert "print('hi')" in md


def test_divider():
    blocks = [_block("divider", "")]
    assert "---" in notion_blocks_to_markdown(blocks)


def test_unsupported_block_skipped():
    blocks = [
        _block("paragraph", "keep"),
        {"type": "table", "table": {}},
    ]
    md = notion_blocks_to_markdown(blocks)
    assert "keep" in md


def test_empty_blocks():
    assert notion_blocks_to_markdown([]) == ""
