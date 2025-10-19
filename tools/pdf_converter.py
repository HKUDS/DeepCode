#!/usr/bin/env python3
"""
PDF Converter Utility

This module provides functionality for converting various document formats to PDF,
including Office documents (.doc, .docx, .ppt, .pptx, .xls, .xlsx) and text files (.txt, .md).

Requirements:
- LibreOffice for Office document conversion
- ReportLab for text-to-PDF conversion
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import tempfile
import shutil
import platform
import os
from pathlib import Path
from typing import Union, Optional, Dict, Any, List


class PDFConverter:
    """
    PDF conversion utility class.

    Provides methods to convert Office documents and text files to PDF format.
    """

    # Define supported file formats
    OFFICE_FORMATS = {".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}
    TEXT_FORMATS = {".txt", ".md"}

    # Class-level logger
    logger = logging.getLogger(__name__)

    def __init__(self) -> None:
        """Initialize the PDF converter."""
        pass

    @staticmethod
    def find_libreoffice_windows() -> Optional[str]:
        """
        Find LibreOffice installation on Windows.
        
        Returns:
            Path to soffice.exe if found, None otherwise
        """
        if platform.system() != "Windows":
            return None
            
        # Common LibreOffice installation paths on Windows
        possible_paths = [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]
        
        # Also check PROGRAMFILES environment variables
        program_files = os.environ.get("PROGRAMFILES")
        program_files_x86 = os.environ.get("PROGRAMFILES(X86)")
        
        if program_files:
            possible_paths.append(os.path.join(program_files, "LibreOffice", "program", "soffice.exe"))
        if program_files_x86:
            possible_paths.append(os.path.join(program_files_x86, "LibreOffice", "program", "soffice.exe"))
        
        # Check each path
        for path in possible_paths:
            if os.path.exists(path):
                return path
                
        return None

    @staticmethod
    def convert_office_to_pdf(
        doc_path: Union[str, Path], output_dir: Optional[str] = None
    ) -> Path:
        """
        Convert Office document (.doc, .docx, .ppt, .pptx, .xls, .xlsx) to PDF.
        Requires LibreOffice to be installed.

        Args:
            doc_path: Path to the Office document file
            output_dir: Output directory for the PDF file

        Returns:
            Path to the generated PDF file
        """
        try:
            # Convert to Path object for easier handling
            doc_path = Path(doc_path)
            if not doc_path.exists():
                raise FileNotFoundError(f"Office document does not exist: {doc_path}")

            name_without_suff = doc_path.stem

            # Prepare output directory
            if output_dir:
                base_output_dir = Path(output_dir)
            else:
                # Generate unique folder name with timestamp to avoid conflicts
                import time
                timestamp = int(time.time())
                folder_name = f"paper_{timestamp}"
                
                # Save to workspace instead of temp directory
                workspace_base = Path(os.getcwd()) / "deepcode_lab" / "papers"
                workspace_base.mkdir(parents=True, exist_ok=True)
                base_output_dir = workspace_base / folder_name

            base_output_dir.mkdir(parents=True, exist_ok=True)

            # Check if LibreOffice is available
            libreoffice_available = False
            working_libreoffice_cmd: Optional[str] = None

            # Prepare subprocess parameters to hide console window on Windows
            subprocess_kwargs: Dict[str, Any] = {
                "capture_output": True,
                "check": True,
                "timeout": 10,
                "encoding": "utf-8",
                "errors": "ignore",
            }

            # Hide console window on Windows
            if platform.system() == "Windows":
                # Use CREATE_NO_WINDOW to prevent console window from appearing
                subprocess_kwargs["creationflags"] = 0x08000000
                # Also configure startupinfo to hide window
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                subprocess_kwargs["startupinfo"] = startupinfo

            # On Windows, try to find LibreOffice in standard installation paths first
            # Don't run --version check on Windows as it can cause window/hanging issues
            if platform.system() == "Windows":
                windows_path = PDFConverter.find_libreoffice_windows()
                if windows_path:
                    libreoffice_available = True
                    working_libreoffice_cmd = windows_path
                    logging.info(f"LibreOffice detected at {windows_path}")

            # On non-Windows systems, try standard commands
            if not libreoffice_available and platform.system() != "Windows":
                try:
                    result = subprocess.run(
                        ["libreoffice", "--version"], **subprocess_kwargs
                    )
                    libreoffice_available = True
                    working_libreoffice_cmd = "libreoffice"
                    logging.info(f"LibreOffice detected: {result.stdout.strip()}")  # type: ignore
                except (
                    subprocess.CalledProcessError,
                    FileNotFoundError,
                    subprocess.TimeoutExpired,
                ):
                    pass

            # Try alternative commands for LibreOffice (non-Windows)
            if not libreoffice_available and platform.system() != "Windows":
                for cmd in ["soffice", "libreoffice"]:
                    try:
                        result = subprocess.run([cmd, "--version"], **subprocess_kwargs)
                        libreoffice_available = True
                        working_libreoffice_cmd = cmd
                        logging.info(
                            f"LibreOffice detected with command '{cmd}': {result.stdout.strip()}"  # type: ignore
                        )
                        break
                    except (
                        subprocess.CalledProcessError,
                        FileNotFoundError,
                        subprocess.TimeoutExpired,
                    ):
                        continue

            if not libreoffice_available:
                raise RuntimeError(
                    "LibreOffice is required for Office document conversion but was not found.\n"
                    "Please install LibreOffice:\n"
                    "- Windows: Download from https://www.libreoffice.org/download/download/\n"
                    "- macOS: brew install --cask libreoffice\n"
                    "- Ubuntu/Debian: sudo apt-get install libreoffice\n"
                    "- CentOS/RHEL: sudo yum install libreoffice\n"
                    "Alternatively, convert the document to PDF manually."
                )

            # Create temporary directory for PDF conversion
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)

                # Convert to PDF using LibreOffice
                logging.info(f"Converting {doc_path.name} to PDF using LibreOffice...")

                # Use the working LibreOffice command first, then try alternatives if it fails
                commands_to_try = [working_libreoffice_cmd]
                
                # Add alternative commands based on what was found
                if platform.system() == "Windows" and working_libreoffice_cmd:
                    # If we're using the full Windows path, also try standard commands
                    if "Program Files" in working_libreoffice_cmd:
                        commands_to_try.extend(["soffice", "libreoffice"])
                elif working_libreoffice_cmd == "libreoffice":
                    commands_to_try.append("soffice")
                else:
                    commands_to_try.append("libreoffice")

                conversion_successful = False
                for cmd in commands_to_try:
                    if cmd is None:
                        continue
                    try:
                        convert_cmd = [
                            cmd,
                            "--headless",
                            "--convert-to",
                            "pdf",
                            "--outdir",
                            str(temp_path),
                            str(doc_path),
                        ]

                        # Prepare conversion subprocess parameters
                        convert_subprocess_kwargs: Dict[str, Any] = {
                            "capture_output": True,
                            "text": True,
                            "timeout": 60,  # 60 second timeout
                            "encoding": "utf-8",
                            "errors": "ignore",
                        }

                        # Hide console window on Windows
                        if platform.system() == "Windows":
                            convert_subprocess_kwargs["creationflags"] = 0x08000000
                            # Also configure startupinfo to hide window
                            startupinfo = subprocess.STARTUPINFO()
                            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                            startupinfo.wShowWindow = subprocess.SW_HIDE
                            convert_subprocess_kwargs["startupinfo"] = startupinfo

                        result = subprocess.run(
                            convert_cmd, **convert_subprocess_kwargs
                        )

                        if result.returncode == 0:  # type: ignore
                            conversion_successful = True
                            logging.info(
                                f"Successfully converted {doc_path.name} to PDF"
                            )
                            break
                        else:
                            logging.warning(
                                f"LibreOffice command '{cmd}' failed: {result.stderr}"  # type: ignore
                            )
                    except subprocess.TimeoutExpired:
                        logging.warning(f"LibreOffice command '{cmd}' timed out")
                    except Exception as e:
                        logging.error(
                            f"LibreOffice command '{cmd}' failed with exception: {e}"
                        )

                if not conversion_successful:
                    raise RuntimeError(
                        f"LibreOffice conversion failed for {doc_path.name}. "
                        f"Please check if the file is corrupted or try converting manually."
                    )

                # Find the generated PDF
                pdf_files = list(temp_path.glob("*.pdf"))
                if not pdf_files:
                    raise RuntimeError(
                        f"PDF conversion failed for {doc_path.name} - no PDF file generated. "
                        f"Please check LibreOffice installation or try manual conversion."
                    )

                pdf_path = pdf_files[0]
                logging.info(
                    f"Generated PDF: {pdf_path.name} ({pdf_path.stat().st_size} bytes)"
                )

                # Validate the generated PDF
                if pdf_path.stat().st_size < 100:  # Very small file, likely empty
                    raise RuntimeError(
                        "Generated PDF appears to be empty or corrupted. "
                        "Original file may have issues or LibreOffice conversion failed."
                    )

                # Copy PDF to final output directory
                final_pdf_path = base_output_dir / f"{name_without_suff}.pdf"
                shutil.copy2(pdf_path, final_pdf_path)
                
                print(f"✅ PDF saved to: {final_pdf_path}")
                print(f"   File size: {final_pdf_path.stat().st_size} bytes")
                print(f"   Parent folder: {base_output_dir}")

                return final_pdf_path

        except Exception as e:
            logging.error(f"Error in convert_office_to_pdf: {str(e)}")
            raise

    @staticmethod
    def convert_text_to_pdf(
        text_path: Union[str, Path], output_dir: Optional[str] = None
    ) -> Path:
        """
        Convert text file (.txt, .md) to PDF using ReportLab with full markdown support.

        Args:
            text_path: Path to the text file
            output_dir: Output directory for the PDF file

        Returns:
            Path to the generated PDF file
        """
        try:
            text_path = Path(text_path)
            if not text_path.exists():
                raise FileNotFoundError(f"Text file does not exist: {text_path}")

            # Supported text formats
            supported_text_formats = {".txt", ".md"}
            if text_path.suffix.lower() not in supported_text_formats:
                raise ValueError(f"Unsupported text format: {text_path.suffix}")

            # Read the text content
            try:
                with open(text_path, "r", encoding="utf-8") as f:
                    text_content = f.read()
            except UnicodeDecodeError:
                # Try with different encodings
                for encoding in ["gbk", "latin-1", "cp1252"]:
                    try:
                        with open(text_path, "r", encoding=encoding) as f:
                            text_content = f.read()
                        logging.info(f"Successfully read file with {encoding} encoding")
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    raise RuntimeError(
                        f"Could not decode text file {text_path.name} with any supported encoding"
                    )

            # Prepare output directory
            if output_dir:
                base_output_dir = Path(output_dir)
            else:
                # Generate unique folder name with timestamp to avoid conflicts
                import time
                timestamp = int(time.time())
                folder_name = f"paper_{timestamp}"
                
                # Save to workspace instead of temp directory
                workspace_base = Path(os.getcwd()) / "deepcode_lab" / "papers"
                workspace_base.mkdir(parents=True, exist_ok=True)
                base_output_dir = workspace_base / folder_name

            base_output_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = base_output_dir / f"{text_path.stem}.pdf"

            # Convert text to PDF
            logging.info(f"Converting {text_path.name} to PDF...")

            try:
                from reportlab.lib.pagesizes import A4
                from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
                from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
                from reportlab.lib.units import inch
                from reportlab.pdfbase import pdfmetrics

                # Create PDF document
                doc = SimpleDocTemplate(
                    str(pdf_path),
                    pagesize=A4,
                    leftMargin=inch,
                    rightMargin=inch,
                    topMargin=inch,
                    bottomMargin=inch,
                )

                # Get styles
                styles = getSampleStyleSheet()
                normal_style = styles["Normal"]
                heading_style = styles["Heading1"]

                # Try to register a font that supports Chinese characters
                try:
                    # Try to use system fonts that support Chinese
                    system = platform.system()
                    if system == "Windows":
                        # Try common Windows fonts
                        for font_name in ["SimSun", "SimHei", "Microsoft YaHei"]:
                            try:
                                from reportlab.pdfbase.cidfonts import (
                                    UnicodeCIDFont,
                                )

                                pdfmetrics.registerFont(UnicodeCIDFont(font_name))  # type: ignore
                                normal_style.fontName = font_name
                                heading_style.fontName = font_name
                                break
                            except Exception:
                                continue
                    elif system == "Darwin":  # macOS
                        for font_name in ["STSong-Light", "STHeiti"]:
                            try:
                                from reportlab.pdfbase.cidfonts import (
                                    UnicodeCIDFont,
                                )

                                pdfmetrics.registerFont(UnicodeCIDFont(font_name))  # type: ignore
                                normal_style.fontName = font_name
                                heading_style.fontName = font_name
                                break
                            except Exception:
                                continue
                except Exception:
                    pass  # Use default fonts if Chinese font setup fails

                # Build content
                story = []

                # Handle markdown or plain text
                if text_path.suffix.lower() == ".md":
                    # Handle markdown content - simplified implementation
                    lines = text_content.split("\n")
                    for line in lines:
                        line = line.strip()
                        if not line:
                            story.append(Spacer(1, 12))
                            continue

                        # Headers
                        if line.startswith("#"):
                            level = len(line) - len(line.lstrip("#"))
                            header_text = line.lstrip("#").strip()
                            if header_text:
                                header_style = ParagraphStyle(
                                    name=f"Heading{level}",
                                    parent=heading_style,
                                    fontSize=max(16 - level, 10),
                                    spaceAfter=8,
                                    spaceBefore=16 if level <= 2 else 12,
                                )
                                story.append(Paragraph(header_text, header_style))
                        else:
                            # Regular text
                            processed_line = PDFConverter._process_inline_markdown(line)
                            story.append(Paragraph(processed_line, normal_style))
                            story.append(Spacer(1, 6))
                else:
                    # Handle plain text files (.txt)
                    logging.info(
                        f"Processing plain text file with {len(text_content)} characters..."
                    )

                    # Split text into lines and process each line
                    lines = text_content.split("\n")
                    line_count = 0

                    for line in lines:
                        line = line.rstrip()
                        line_count += 1

                        # Empty lines
                        if not line.strip():
                            story.append(Spacer(1, 6))
                            continue

                        # Regular text lines
                        # Escape special characters for ReportLab
                        safe_line = (
                            line.replace("&", "&amp;")
                            .replace("<", "&lt;")
                            .replace(">", "&gt;")
                        )

                        # Create paragraph
                        story.append(Paragraph(safe_line, normal_style))
                        story.append(Spacer(1, 3))

                    logging.info(f"Added {line_count} lines to PDF")

                    # If no content was added, add a placeholder
                    if not story:
                        story.append(Paragraph("(Empty text file)", normal_style))

                # Build PDF
                doc.build(story)
                logging.info(
                    f"Successfully converted {text_path.name} to PDF ({pdf_path.stat().st_size / 1024:.1f} KB)"
                )

            except ImportError:
                raise RuntimeError(
                    "reportlab is required for text-to-PDF conversion. "
                    "Please install it using: pip install reportlab"
                )
            except Exception as e:
                raise RuntimeError(
                    f"Failed to convert text file {text_path.name} to PDF: {str(e)}"
                )

            # Validate the generated PDF
            if not pdf_path.exists() or pdf_path.stat().st_size < 100:
                raise RuntimeError(
                    f"PDF conversion failed for {text_path.name} - generated PDF is empty or corrupted."
                )

            print(f"✅ PDF saved to: {pdf_path}")
            print(f"   File size: {pdf_path.stat().st_size} bytes")
            print(f"   Parent folder: {base_output_dir}")
            
            return pdf_path

        except Exception as e:
            logging.error(f"Error in convert_text_to_pdf: {str(e)}")
            raise

    @staticmethod
    def _process_inline_markdown(text: str) -> str:
        """
        Process inline markdown formatting (bold, italic, code, links)

        Args:
            text: Raw text with markdown formatting

        Returns:
            Text with ReportLab markup
        """
        import re

        # Escape special characters for ReportLab
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        # Bold text: **text** or __text__
        text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"__(.*?)__", r"<b>\1</b>", text)

        # Italic text: *text* or _text_ (but not in the middle of words)
        text = re.sub(r"(?<!\w)\*([^*\n]+?)\*(?!\w)", r"<i>\1</i>", text)
        text = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"<i>\1</i>", text)

        # Inline code: `code`
        text = re.sub(
            r"`([^`]+?)`",
            r'<font name="Courier" size="9" color="darkred">\1</font>',
            text,
        )

        # Links: [text](url) - convert to text with URL annotation
        def link_replacer(match):
            link_text = match.group(1)
            url = match.group(2)
            return f'<link href="{url}" color="blue"><u>{link_text}</u></link>'

        text = re.sub(r"\[([^\]]+?)\]\(([^)]+?)\)", link_replacer, text)

        # Strikethrough: ~~text~~
        text = re.sub(r"~~(.*?)~~", r"<strike>\1</strike>", text)

        return text

    def convert_to_pdf(
        self,
        file_path: Union[str, Path],
        output_dir: Optional[str] = None,
    ) -> Path:
        """
        Convert document to PDF based on file extension

        Args:
            file_path: Path to the file to be converted
            output_dir: Output directory path

        Returns:
            Path to the generated PDF file
        """
        # Convert to Path object
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File does not exist: {file_path}")

        # Get file extension
        ext = file_path.suffix.lower()

        # Choose appropriate conversion method based on file type
        if ext in self.OFFICE_FORMATS:
            return self.convert_office_to_pdf(file_path, output_dir)
        elif ext in self.TEXT_FORMATS:
            return self.convert_text_to_pdf(file_path, output_dir)
        else:
            raise ValueError(
                f"Unsupported file format: {ext}. "
                f"Supported formats: {', '.join(self.OFFICE_FORMATS | self.TEXT_FORMATS)}"
            )

    def check_dependencies(self) -> dict:
        """
        Check if required dependencies are available

        Returns:
            dict: Dictionary with dependency check results
        """
        results = {
            "libreoffice": False,
            "reportlab": False,
        }

        # Check LibreOffice
        # On Windows, just check if the executable exists (don't run it to avoid window issues)
        if platform.system() == "Windows":
            windows_path = PDFConverter.find_libreoffice_windows()
            if windows_path:
                results["libreoffice"] = True
        else:
            # On non-Windows systems, try running the version command
            try:
                subprocess_kwargs: Dict[str, Any] = {
                    "capture_output": True,
                    "text": True,
                    "check": True,
                    "timeout": 5,
                    "encoding": "utf-8",
                    "errors": "ignore",
                }

                try:
                    subprocess.run(["libreoffice", "--version"], **subprocess_kwargs)
                    results["libreoffice"] = True
                except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                    try:
                        subprocess.run(["soffice", "--version"], **subprocess_kwargs)
                        results["libreoffice"] = True
                    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                        pass
            except Exception:
                # If any unexpected error occurs during LibreOffice check, silently pass
                pass

        # Check ReportLab
        import importlib.util

        if importlib.util.find_spec("reportlab") is not None:
            results["reportlab"] = True

        return results


def main():
    """
    Main function to run the PDF converter from command line
    """
    parser = argparse.ArgumentParser(description="Convert documents to PDF format")
    parser.add_argument("file_path", nargs="?", help="Path to the document to convert")
    parser.add_argument("--output", "-o", help="Output directory path")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check dependencies installation",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )

    args = parser.parse_args()

    # Configure logging
    log_level = logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Initialize converter
    converter = PDFConverter()

    # Check dependencies if requested
    if args.check:
        print("🔍 Checking dependencies...")
        deps = converter.check_dependencies()

        print(
            f"LibreOffice: {'✅ Available' if deps['libreoffice'] else '❌ Not found'}"
        )
        print(f"ReportLab: {'✅ Available' if deps['reportlab'] else '❌ Not found'}")

        if not deps["libreoffice"]:
            print("\n📋 To install LibreOffice:")
            print("  - Windows: Download from https://www.libreoffice.org/")
            print("  - macOS: brew install --cask libreoffice")
            print("  - Ubuntu/Debian: sudo apt-get install libreoffice")

        if not deps["reportlab"]:
            print("\n📋 To install ReportLab:")
            print("  pip install reportlab")

        return 0

    # If not checking dependencies, file_path is required
    if not args.file_path:
        parser.error("file_path is required when not using --check")

    try:
        # Convert the file
        output_pdf = converter.convert_to_pdf(
            file_path=args.file_path,
            output_dir=args.output,
        )

        print(f"✅ Successfully converted to PDF: {output_pdf}")
        print(f"📄 File size: {output_pdf.stat().st_size / 1024:.1f} KB")

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
