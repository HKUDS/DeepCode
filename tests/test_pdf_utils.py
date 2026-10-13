"""PDF metadata reading."""

from __future__ import annotations

import pypdf
import pytest

from tools.pdf_utils import read_pdf_metadata


def _pdf(tmp_path, **metadata):
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=72, height=72)
    if metadata:
        writer.add_metadata(metadata)
    path = tmp_path / "paper.pdf"
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


@pytest.mark.parametrize(
    ("created", "year"),
    [
        ("D:20231012093000+08'00'", "2023"),
        ("D:1999", "1999"),
        ("20231012", "2023"),  # the "D:" prefix is optional
        ("yesterday", "Unknown Year"),
    ],
)
def test_year_comes_from_the_pdf_creation_date(tmp_path, created, year):
    """It took the first four characters, so "D:2023..." gave the year "D:20"."""
    path = _pdf(tmp_path, **{"/CreationDate": created})

    assert read_pdf_metadata(path)["year"] == year


def test_year_is_unknown_without_a_creation_date(tmp_path):
    path = _pdf(tmp_path, **{"/Title": "A paper"})

    assert read_pdf_metadata(path)["year"] == "Unknown Year"
