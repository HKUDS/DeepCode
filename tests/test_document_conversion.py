from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import pytest

from tools import pdf_downloader
from tools.document_conversion import (
    UnsupportedDocumentError,
    convert_to_markdown,
    detect_document_kind,
)
from workflows.agent_orchestration_engine import acquire_input_artifact
from workflows.environment import _normalize_input
from workflows.workflow_context import WorkflowContext


def test_converts_plain_text_without_modifying_the_source(tmp_path: Path) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("Research notes\n\nA reproducible method.", encoding="utf-8")

    result = convert_to_markdown(source)

    assert source.read_text(encoding="utf-8").startswith("Research notes")
    assert result.input_kind == "text"
    assert result.output_file == tmp_path / "notes.md"
    assert result.output_file.read_text(encoding="utf-8") == (
        "Research notes\n\nA reproducible method.\n"
    )


def test_converts_html_structure_and_discards_active_content(tmp_path: Path) -> None:
    source = tmp_path / "paper.html"
    source.write_text(
        """
        <!doctype html>
        <html>
          <head><style>.hidden { display: none }</style></head>
          <body>
            <h1>Paper Title</h1>
            <p>A <a href="https://example.com/reference">reference</a>.</p>
            <ul><li>First item</li><li>Second item</li></ul>
            <script>alert("not document content")</script>
            <a href="javascript:alert(1)">unsafe</a>
          </body>
        </html>
        """,
        encoding="utf-8",
    )

    output = convert_to_markdown(source).output_file.read_text(encoding="utf-8")

    assert "# Paper Title" in output
    assert "reference (https://example.com/reference)" in output
    assert "- First item" in output
    assert "- Second item" in output
    assert "not document content" not in output
    assert "javascript:" not in output


def test_converts_docx_headings_paragraphs_and_tables(tmp_path: Path) -> None:
    source = tmp_path / "paper.docx"
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p>
          <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
          <w:r><w:t>Method</w:t></w:r>
        </w:p>
        <w:p><w:r><w:t>Train the model deterministically.</w:t></w:r></w:p>
        <w:tbl>
          <w:tr>
            <w:tc><w:p><w:r><w:t>Setting</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>Value</w:t></w:r></w:p></w:tc>
          </w:tr>
          <w:tr>
            <w:tc><w:p><w:r><w:t>Seed</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>42</w:t></w:r></w:p></w:tc>
          </w:tr>
        </w:tbl>
      </w:body>
    </w:document>
    """
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", document_xml)

    output = convert_to_markdown(source).output_file.read_text(encoding="utf-8")

    assert "# Method" in output
    assert "Train the model deterministically." in output
    assert "| Setting | Value |" in output
    assert "| Seed | 42 |" in output


def test_detects_html_returned_under_a_pdf_filename(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    source.write_text("<!doctype html><h1>Web paper</h1>", encoding="utf-8")

    assert detect_document_kind(source) == "html"
    assert "# Web paper" in convert_to_markdown(source).output_file.read_text(
        encoding="utf-8"
    )


def test_rejects_legacy_doc_instead_of_advertising_a_broken_format(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.doc"
    source.write_bytes(b"\xd0\xcf\x11\xe0binary")

    with pytest.raises(UnsupportedDocumentError, match="save.*\\.docx"):
        detect_document_kind(source)
    with pytest.raises(ValueError, match="unsupported extension"):
        _normalize_input(str(source))


@pytest.mark.asyncio
async def test_pdf_downloader_uses_built_in_converter_without_docling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "paper.txt"
    source.write_text("Portable baseline conversion", encoding="utf-8")
    monkeypatch.setattr(pdf_downloader, "DOCLING_AVAILABLE", False)

    message = await pdf_downloader.perform_document_conversion(str(source))

    assert message is not None
    assert "built-in" in message
    assert (tmp_path / "paper.md").read_text(encoding="utf-8") == (
        "Portable baseline conversion\n"
    )


@pytest.mark.asyncio
async def test_direct_conversion_tool_uses_shared_path_and_honors_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "paper.html"
    target = tmp_path / "converted.md"
    source.write_text("<h1>Direct conversion</h1>", encoding="utf-8")
    monkeypatch.setattr(pdf_downloader, "DOCLING_AVAILABLE", False)

    message = await pdf_downloader.convert_document_to_markdown(
        str(source),
        str(target),
    )

    assert "[SUCCESS]" in message
    assert target.read_text(encoding="utf-8") == "# Direct conversion\n"


@pytest.mark.asyncio
async def test_phase_two_requires_and_records_markdown_artifact(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("Workflow-ready text", encoding="utf-8")
    task_dir = tmp_path / "workspace" / "tasks" / "paper_test"
    task_dir.mkdir(parents=True)
    context = WorkflowContext(
        task_id="test",
        input_source=str(source),
        input_kind="txt",
        workspace_root=tmp_path / "workspace",
        task_dir=task_dir,
        enable_indexing=False,
    )

    await acquire_input_artifact(context, logging.getLogger("document-conversion"))

    assert context.paper_path == task_dir / "paper.txt"
    assert context.paper_md_path == task_dir / "paper.md"
    assert context.paper_md_path.read_text(encoding="utf-8") == (
        "Workflow-ready text\n"
    )
