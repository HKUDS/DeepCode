"""
PDF utility functions for the DeepCode agent system.
"""

import re
from pathlib import Path
import pypdf

# A PDF date is "D:YYYYMMDDHHmmSSOHH'mm'": the "D:" prefix is optional and
# everything after the year may be left out.
_PDF_DATE_YEAR = re.compile(r"(?:D:)?(\d{4})")


def _pdf_year(date) -> str:
    match = _PDF_DATE_YEAR.match(str(date or "").strip())
    return match.group(1) if match else "Unknown Year"


def read_pdf_metadata(file_path: Path) -> dict:
    """Read PDF metadata with proper encoding handling."""
    try:
        print(f"\nAttempting to read PDF metadata from: {file_path}")
        with open(file_path, "rb") as file:
            pdf_reader = pypdf.PdfReader(file)
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
                "year": _pdf_year(info.get("/CreationDate") if info else None),
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
