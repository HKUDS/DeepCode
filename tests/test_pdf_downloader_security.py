from __future__ import annotations

from pathlib import Path

import pytest

from tools import pdf_downloader


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:pass@example.com/paper.pdf",
        "http://127.0.0.1/paper.pdf",
        "http://[::1]/paper.pdf",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.1/internal.pdf",
    ],
)
def test_rejects_non_http_credentials_and_private_literal_urls(url: str) -> None:
    with pytest.raises(ValueError):
        pdf_downloader._validate_public_url(url)


@pytest.mark.asyncio
async def test_download_fails_closed_before_connecting_to_loopback(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "paper.pdf"
    result = await pdf_downloader.download_file(
        "http://127.0.0.1:9/paper.pdf", str(destination)
    )
    assert result["success"] is False
    assert not destination.exists()
    assert not (tmp_path / "paper.pdf.part").exists()


@pytest.mark.asyncio
async def test_docling_is_preferred_over_pdf_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-test")

    class FakeDoclingConverter:
        def is_supported_format(self, _path):
            return True

        def convert_to_markdown(self, path, *, extract_images):
            output = Path(path).with_suffix(".md")
            output.write_text("docling", encoding="utf-8")
            return {
                "success": True,
                "output_file": str(output),
                "duration": 0.01,
                "images_extracted": 2 if extract_images else 0,
            }

    class FailingFallback:
        def convert_pdf_to_markdown(self, _path):
            raise AssertionError("PyPDF fallback must not run after Docling succeeds")

    monkeypatch.setattr(pdf_downloader, "DOCLING_AVAILABLE", True)
    monkeypatch.setattr(pdf_downloader, "DoclingConverter", FakeDoclingConverter)
    monkeypatch.setattr(pdf_downloader, "SimplePdfConverter", FailingFallback)

    message = await pdf_downloader.perform_document_conversion(str(source))
    assert message is not None
    assert "Docling" in message
    assert "Images extracted: 2" in message
