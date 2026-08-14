"""Small, deterministic document-to-Markdown converters.

The desktop runtime must be able to process every format it advertises without
depending on a system Office installation or the very large optional Docling
stack.  This module therefore owns the reliable baseline conversion path for
Markdown, plain text, HTML, and modern DOCX files.  PDF extraction remains in
``tools.pdf_downloader`` because it has a dedicated pypdf fallback.

Only document contents are read.  DOCX archives are never extracted to disk,
and HTML scripts/styles are discarded before text reaches the Agent workflow.
"""

from __future__ import annotations

import os
import re
import uuid
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from xml.etree import ElementTree


DocumentKind = Literal["markdown", "text", "html", "docx", "pdf"]

_MARKDOWN_SUFFIXES = {".md", ".markdown"}
_TEXT_SUFFIXES = {".txt"}
_HTML_SUFFIXES = {".html", ".htm"}
_DOCX_SUFFIXES = {".docx"}
_MAX_DOCX_XML_BYTES = 32 * 1024 * 1024
_HTML_PREFIX_RE = re.compile(
    rb"^\s*(?:<!doctype\s+html\b|<html\b|<head\b|<body\b)",
    re.IGNORECASE,
)
_HEADING_STYLE_RE = re.compile(r"heading\s*([1-6])", re.IGNORECASE)


class DocumentConversionError(ValueError):
    """Raised when a supported document cannot be converted safely."""


class UnsupportedDocumentError(DocumentConversionError):
    """Raised when the baseline converter cannot identify the input format."""


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """Result of a successful baseline conversion."""

    output_file: Path
    input_kind: DocumentKind
    character_count: int


def detect_document_kind(path: str | Path) -> DocumentKind:
    """Identify a supported document using magic bytes before its suffix."""

    source = Path(path)
    try:
        with source.open("rb") as handle:
            header = handle.read(8192)
    except OSError as exc:
        raise DocumentConversionError(f"could not read document: {exc}") from exc

    if header.startswith(b"%PDF"):
        return "pdf"

    suffix = source.suffix.lower()
    if suffix == ".doc":
        raise UnsupportedDocumentError(
            "legacy .doc files are not supported; save the document as .docx"
        )
    if suffix in _DOCX_SUFFIXES or _looks_like_docx(source, header):
        return "docx"
    if suffix in _MARKDOWN_SUFFIXES:
        return "markdown"
    if suffix in _HTML_SUFFIXES or _HTML_PREFIX_RE.match(header):
        return "html"
    if suffix in _TEXT_SUFFIXES:
        return "text"

    # A URL can legitimately return text or HTML under an unhelpful filename.
    # Accept it only when the payload is clearly text; arbitrary binary input
    # still fails closed.
    text = _decode_text(header)
    if _looks_like_html_text(text):
        return "html"
    if _looks_like_plain_text(text):
        return "text"
    raise UnsupportedDocumentError(
        f"unsupported document format: {suffix or '(no extension)'}"
    )


def convert_to_markdown(
    input_file: str | Path,
    output_file: str | Path | None = None,
) -> ConversionResult:
    """Convert a baseline-supported document to a UTF-8 Markdown file."""

    source = Path(input_file).expanduser().resolve(strict=True)
    kind = detect_document_kind(source)
    if kind == "pdf":
        raise UnsupportedDocumentError("PDF conversion is handled by the PDF extractor")

    target = (
        Path(output_file).expanduser().resolve()
        if output_file is not None
        else source.with_suffix(".md")
    )
    if kind == "markdown":
        markdown = _read_text(source)
    elif kind == "text":
        markdown = _read_text(source)
    elif kind == "html":
        markdown = _html_to_markdown(_read_text(source))
    else:
        markdown = _docx_to_markdown(source)

    normalized = _normalize_markdown(markdown)
    if not normalized.strip():
        raise DocumentConversionError("document contains no readable text")
    if target != source:
        _atomic_write_text(target, normalized)
    return ConversionResult(
        output_file=target,
        input_kind=kind,
        character_count=len(normalized),
    )


def _looks_like_docx(path: Path, header: bytes) -> bool:
    if not header.startswith(b"PK"):
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            return "word/document.xml" in archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def _read_text(path: Path) -> str:
    try:
        return _decode_text(path.read_bytes())
    except OSError as exc:
        raise DocumentConversionError(f"could not read text document: {exc}") from exc


def _decode_text(data: bytes) -> str:
    if b"\x00" in data and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise UnsupportedDocumentError("document payload is binary")
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        return data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise DocumentConversionError("text document is not UTF-8 or UTF-16") from exc


def _looks_like_html_text(text: str) -> bool:
    prefix = text.lstrip()[:256].lower()
    return prefix.startswith(("<!doctype html", "<html", "<head", "<body"))


def _looks_like_plain_text(text: str) -> bool:
    if not text:
        return True
    control_count = sum(
        1 for char in text if ord(char) < 32 and char not in "\n\r\t\f\b"
    )
    return control_count / len(text) < 0.01


class _MarkdownHTMLParser(HTMLParser):
    """Conservative HTML reader that keeps structure but ignores active content."""

    _SKIPPED_TAGS = {"script", "style", "noscript", "template", "svg"}
    _BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "div",
        "footer",
        "header",
        "main",
        "nav",
        "p",
        "section",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.pre_depth = 0
        self.links: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIPPED_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        attributes = dict(attrs)
        if tag in self._BLOCK_TAGS:
            self._break(paragraph=True)
        elif tag in {f"h{level}" for level in range(1, 7)}:
            self._break(paragraph=True)
            self.parts.append(f"{'#' * int(tag[1])} ")
        elif tag == "br":
            self._break()
        elif tag == "li":
            self._break()
            self.parts.append("- ")
        elif tag == "hr":
            self._break(paragraph=True)
            self.parts.append("---")
            self._break(paragraph=True)
        elif tag == "pre":
            self._break(paragraph=True)
            self.parts.append("```\n")
            self.pre_depth += 1
        elif tag == "code" and not self.pre_depth:
            self.parts.append("`")
        elif tag == "a":
            self.links.append(_safe_link(attributes.get("href")))
        elif tag in {"tr"}:
            self._break()
        elif tag in {"td", "th"}:
            self.parts.append("| ")
        elif tag == "img":
            alt = (attributes.get("alt") or "").strip()
            if alt:
                self.parts.append(f"[Image: {alt}]")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIPPED_TAGS:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag in self._BLOCK_TAGS or tag.startswith("h") and tag[1:].isdigit():
            self._break(paragraph=True)
        elif tag in {"li", "tr"}:
            if tag == "tr":
                self.parts.append("|")
            self._break()
        elif tag in {"td", "th"}:
            self.parts.append(" ")
        elif tag == "pre":
            if self.pre_depth:
                self.pre_depth -= 1
            self._break()
            self.parts.append("```")
            self._break(paragraph=True)
        elif tag == "code" and not self.pre_depth:
            self.parts.append("`")
        elif tag == "a" and self.links:
            href = self.links.pop()
            if href:
                self.parts.append(f" ({href})")

    def handle_data(self, data: str) -> None:
        if self.skip_depth or not data:
            return
        if self.pre_depth:
            self.parts.append(data)
            return
        collapsed = re.sub(r"\s+", " ", data)
        if not collapsed.strip():
            if self.parts and not self.parts[-1].endswith(("\n", " ")):
                self.parts.append(" ")
            return
        if (
            self.parts
            and not self.parts[-1].endswith(("\n", " ", "`"))
            and not collapsed.startswith((" ", ".", ",", ":", ";", "!", "?"))
        ):
            self.parts.append(" ")
        self.parts.append(collapsed.strip())

    def _break(self, *, paragraph: bool = False) -> None:
        desired = "\n\n" if paragraph else "\n"
        current = "".join(self.parts[-2:])
        if not current.endswith(desired):
            self.parts.append(desired)


def _safe_link(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme.lower() in {"javascript", "data", "file"}:
        return None
    return cleaned


def _html_to_markdown(html: str) -> str:
    parser = _MarkdownHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:
        raise DocumentConversionError(f"could not parse HTML document: {exc}") from exc
    return "".join(parser.parts)


def _docx_to_markdown(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            try:
                info = archive.getinfo("word/document.xml")
            except KeyError as exc:
                raise DocumentConversionError(
                    "DOCX archive does not contain word/document.xml"
                ) from exc
            if info.file_size > _MAX_DOCX_XML_BYTES:
                raise DocumentConversionError(
                    "DOCX document XML exceeds the safe conversion limit"
                )
            xml = archive.read(info)
    except zipfile.BadZipFile as exc:
        raise DocumentConversionError("DOCX file is not a valid ZIP archive") from exc
    except OSError as exc:
        raise DocumentConversionError(f"could not read DOCX file: {exc}") from exc

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise DocumentConversionError(f"DOCX XML is invalid: {exc}") from exc

    body = next((node for node in root.iter() if _local_name(node.tag) == "body"), None)
    if body is None:
        raise DocumentConversionError("DOCX document body is missing")

    blocks: list[str] = []
    for child in body:
        name = _local_name(child.tag)
        if name == "p":
            paragraph = _docx_paragraph(child)
            if paragraph:
                blocks.append(paragraph)
        elif name == "tbl":
            table = _docx_table(child)
            if table:
                blocks.append(table)
    return "\n\n".join(blocks)


def _docx_paragraph(node: ElementTree.Element) -> str:
    text = _docx_text(node).strip()
    if not text:
        return ""
    style = ""
    numbered = False
    for descendant in node.iter():
        name = _local_name(descendant.tag)
        if name == "pStyle":
            style = _xml_attribute(descendant, "val") or ""
        elif name == "numPr":
            numbered = True
    heading = _HEADING_STYLE_RE.search(style)
    if heading:
        return f"{'#' * int(heading.group(1))} {text}"
    if style.lower() == "title":
        return f"# {text}"
    if numbered or "list" in style.lower():
        return f"- {text}"
    return text


def _docx_table(node: ElementTree.Element) -> str:
    rows: list[list[str]] = []
    for row in node:
        if _local_name(row.tag) != "tr":
            continue
        cells: list[str] = []
        for cell in row:
            if _local_name(cell.tag) != "tc":
                continue
            value = " ".join(
                text
                for paragraph in cell
                if _local_name(paragraph.tag) == "p"
                and (text := _docx_text(paragraph).strip())
            )
            cells.append(value.replace("|", "\\|"))
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(row) + " |" for row in padded]
    lines.insert(1, "| " + " | ".join("---" for _ in range(width)) + " |")
    return "\n".join(lines)


def _docx_text(node: ElementTree.Element) -> str:
    parts: list[str] = []
    for descendant in node.iter():
        name = _local_name(descendant.tag)
        if name in {"t", "delText"} and descendant.text:
            parts.append(descendant.text)
        elif name == "tab":
            parts.append("\t")
        elif name in {"br", "cr"}:
            parts.append("\n")
    return "".join(parts)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_attribute(node: ElementTree.Element, name: str) -> str | None:
    for key, value in node.attrib.items():
        if _local_name(key) == name:
            return value
    return None


def _normalize_markdown(markdown: str) -> str:
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.splitlines()]
    normalized = "\n".join(lines).strip()
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return f"{normalized}\n" if normalized else ""


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
