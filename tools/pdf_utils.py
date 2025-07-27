"""
PDF utility functions for the DeepCode agent system.
"""

from pathlib import Path
import PyPDF2


def read_pdf_metadata(file_path: Path) -> dict:
    """Read PDF metadata with proper encoding handling."""
    try:
        print(f"\nAttempting to read PDF metadata from: {file_path}")
        with open(file_path, "rb") as file:
            pdf_reader = PyPDF2.PdfReader(file)
            info = pdf_reader.metadata
            first_page = pdf_reader.pages[0]
            text = first_page.extract_text()
            lines = text.split("\n")[:10]

            title = None
            authors = []

            if info:
                title = info.get("/Title", "").strip().replace("\x00", "")
                author = info.get("/Author", "").strip().replace("\x00", "")
                if author:
                    authors = [author]

            if not title and lines:
                title = lines[0].strip()

            if not authors and len(lines) > 1:
                for line in lines[1:3]:
                    if "author" in line.lower() or "by" in line.lower():
                        authors = [line.strip()]
                        break

            return {
                "title": title if title else "Unknown Title",
                "authors": authors if authors else ["Unknown Author"],
                "year": info.get("/CreationDate", "")[:4] if info else "Unknown Year",
                "first_lines": lines,
            }

    except Exception as e:
        print(f"\nError reading PDF: {str(e)}")
        return {
            "title": "Error reading PDF",
            "authors": ["Unknown"],
            "year": "Unknown",
            "first_lines": [],
        }
