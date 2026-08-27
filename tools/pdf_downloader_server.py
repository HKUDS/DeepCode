"""MCP process entry point for the reusable PDF download helpers.

The workflow runtime imports :mod:`tools.pdf_downloader` as a normal Python
library. Keeping FastMCP registration in this dedicated entry point prevents
library consumers such as the Desktop App Server from importing and packaging
the entire MCP server stack.
"""

from __future__ import annotations

import sys

from mcp.server import FastMCP

from tools.pdf_downloader import (
    DOCLING_AVAILABLE,
    convert_document_to_markdown,
    download_file_to,
    download_files,
    move_file_to,
    parse_download_urls,
)


def build_server() -> FastMCP:
    server = FastMCP("smart-pdf-downloader")
    for tool in (
        download_files,
        parse_download_urls,
        download_file_to,
        move_file_to,
        convert_document_to_markdown,
    ):
        server.tool()(tool)
    return server


def run() -> None:
    protocol_stdout = sys.stdout
    sys.stdout = sys.stderr
    print("📄 Smart PDF Downloader MCP Tool")
    print("📝 Starting server with FastMCP...")

    if DOCLING_AVAILABLE:
        print("✅ Advanced document conversion is enabled (Docling available)")
    else:
        print("✅ Baseline PDF, Markdown, TXT, HTML and DOCX conversion is enabled")
        print("   Install deepcode-hku[advanced-documents] for Docling features")

    print("\nAvailable tools:")
    print(
        "  • download_files - Download files or move local files from natural language"
    )
    print("  • parse_download_urls - Extract URLs, local paths and destination paths")
    print("  • download_file_to - Download a specific file with options")
    print("  • move_file_to - Move a specific local file with options")
    print("  • convert_document_to_markdown - Convert documents to Markdown format")

    print("\nBaseline formats: PDF, DOCX, HTML, TXT, MD")
    if DOCLING_AVAILABLE:
        print("Advanced formats/features: PPTX, image extraction, layout preservation")

    print()
    sys.stdout = protocol_stdout
    build_server().run()


if __name__ == "__main__":
    run()
