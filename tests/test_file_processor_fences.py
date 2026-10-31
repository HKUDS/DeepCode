"""Paper headings must not be inferred from fenced code examples."""

import asyncio

import pytest

from utils.file_processor import FileProcessor


@pytest.mark.parametrize("fence", ["```python", "~~~python", "   ```python"])
def test_fenced_comments_stay_in_their_paper_section(fence):
    marker = "~~~" if "~" in fence else "```"
    code = f"{fence}\n# initialize the model\nx = 1\n{marker}"
    sections = FileProcessor.parse_markdown_sections(
        f"# Paper\n\n{code}\n\n## Results\nAccurate."
    )
    assert len(sections) == 1
    assert sections[0]["title"] == "Paper"
    assert sections[0]["content"] == code.strip()
    assert [s["title"] for s in sections[0]["subsections"]] == ["Results"]


@pytest.mark.parametrize(
    "non_closer", ["```", "~~~~", "```` trailing text", "    ````"]
)
def test_only_a_matching_long_enough_fence_closes_code(non_closer):
    code = f"````python\n{non_closer}\n# still a comment\n`````"
    sections = FileProcessor.parse_markdown_sections(
        f"# Paper\n{code}\n## Results\nDone."
    )
    assert len(sections) == 1
    assert sections[0]["content"] == code.strip()
    assert sections[0]["subsections"][0]["title"] == "Results"


def test_unclosed_fence_keeps_later_hash_lines_as_code():
    sections = FileProcessor.parse_markdown_sections(
        "# Paper\n```python\n# code comment"
    )
    assert len(sections) == 1
    assert sections[0]["content"] == "```python\n# code comment"


def test_backticks_in_info_string_do_not_open_a_fence():
    sections = FileProcessor.parse_markdown_sections(
        "# Paper\n```not`a-fence\n## Results"
    )
    assert sections[0]["subsections"][0]["title"] == "Results"


def test_process_file_input_preserves_code_before_standardization(tmp_path):
    paper = tmp_path / "paper.md"
    code = "```python\n# initialize the model\nx = 1\n```"
    paper.write_text(f"# Paper\n{code}\n## Results\nDone.", encoding="utf-8")
    result = asyncio.run(FileProcessor.process_file_input(str(paper)))
    assert len(result["sections"]) == 1
    assert code in result["standardized_text"]
    assert result["sections"][0]["subsections"][0]["title"] == "Results"


def test_crlf_fences_close_before_the_next_heading():
    sections = FileProcessor.parse_markdown_sections(
        "# Paper\r\n```python\r\n# code comment\r\n```\r\n## Results\r\nDone."
    )
    assert len(sections) == 1
    assert sections[0]["subsections"][0]["title"] == "Results"
    assert "# code comment" in sections[0]["content"]
