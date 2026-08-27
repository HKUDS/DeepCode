#!/usr/bin/env python3
"""
Smart PDF Downloader MCP Tool

A standardized MCP tool using FastMCP for intelligent file downloading and document conversion.
Supports natural language instructions for downloading files from URLs, moving local files,
and automatic conversion to Markdown format with image extraction.

Features:
- Natural language instruction parsing
- URL and local path extraction
- Automatic document conversion (PDF, DOCX, PPTX, HTML, etc.)
- Image extraction and preservation
- Multi-format support with fallback options
"""

import asyncio
import ipaddress
import os
import re
import shutil
import socket
import sys
from datetime import datetime
from importlib import import_module
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import aiofiles
import aiohttp

from core.platform_compat import configure_utf8_stdio
from tools.document_conversion import (
    DocumentConversionError,
    convert_to_markdown,
    detect_document_kind,
)

configure_utf8_stdio()

# Docling imports for document conversion
try:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    DOCLING_AVAILABLE = True
except ImportError:
    DOCLING_AVAILABLE = False
    print(
        "Info: docling is unavailable; built-in document converters remain enabled.",
        file=sys.stderr,
    )

# Fallback PDF text extraction
try:
    import pypdf

    PDF_FALLBACK_AVAILABLE = True
except ImportError:
    PDF_FALLBACK_AVAILABLE = False
    print(
        "Warning: pypdf is not available. Fallback PDF extraction is disabled.",
        file=sys.stderr,
    )

# 设置标准输出编码为UTF-8
_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
_MAX_REDIRECTS = 5


class _SafeResolver(aiohttp.abc.AbstractResolver):
    """Resolve only public IPs so the connection cannot DNS-rebind to localhost."""

    async def resolve(self, host, port=0, family=socket.AF_UNSPEC):
        loop = asyncio.get_running_loop()
        records = await loop.getaddrinfo(
            host, port, type=socket.SOCK_STREAM, family=family
        )
        results = []
        for resolved_family, _, proto, _, address in records:
            ip = ipaddress.ip_address(address[0])
            if not _is_public_ip(ip):
                raise OSError(f"URL resolves to a non-public address: {ip}")
            results.append(
                {
                    "hostname": host,
                    "host": str(ip),
                    "port": address[1],
                    "family": resolved_family,
                    "proto": proto,
                    "flags": socket.AI_NUMERICHOST,
                }
            )
        if not results:
            raise OSError(f"URL host did not resolve: {host}")
        return results

    async def close(self):
        return None


def _is_public_ip(value: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(value, ipaddress.IPv6Address) and value.ipv4_mapped is not None:
        value = value.ipv4_mapped
    return not (
        value.is_private
        or value.is_loopback
        or value.is_link_local
        or value.is_multicast
        or value.is_reserved
        or value.is_unspecified
    )


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("only HTTP and HTTPS URLs are supported")
    if parsed.username or parsed.password:
        raise ValueError("URL credentials are not allowed")
    try:
        literal = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        literal = None
    if literal is not None and not _is_public_ip(literal):
        raise ValueError("URL host must use a public address")


async def _request_with_safe_redirects(
    session: aiohttp.ClientSession, method: str, url: str
) -> aiohttp.ClientResponse:
    current = url
    for redirect_count in range(_MAX_REDIRECTS + 1):
        _validate_public_url(current)
        response = await session.request(method, current, allow_redirects=False)
        if response.status not in {301, 302, 303, 307, 308}:
            return response
        location = response.headers.get("Location")
        response.release()
        if not location:
            raise aiohttp.ClientResponseError(
                response.request_info,
                response.history,
                status=response.status,
                message="redirect response has no Location header",
            )
        if redirect_count == _MAX_REDIRECTS:
            raise ValueError("URL exceeded the redirect limit")
        current = urljoin(current, location)
    raise AssertionError("unreachable redirect loop")


# 辅助函数
def format_success_message(action: str, details: dict[str, Any]) -> str:
    """格式化成功消息"""
    return f"✅ {action}\n" + "\n".join(f"   {k}: {v}" for k, v in details.items())


def format_error_message(action: str, error: str) -> str:
    """格式化错误消息"""
    return f"❌ {action}\n   Error: {error}"


def format_warning_message(action: str, warning: str) -> str:
    """格式化警告消息"""
    return f"⚠️ {action}\n   Warning: {warning}"


async def perform_document_conversion(
    file_path: str,
    extract_images: bool = True,
    output_path: str | None = None,
) -> str | None:
    """
    执行文档转换的共用逻辑

    Args:
        file_path: 文件路径
        extract_images: 是否提取图片

    Returns:
        转换信息字符串，如果没有转换则返回None
    """
    if not file_path:
        return None

    warnings: list[str] = []
    if DOCLING_AVAILABLE:
        try:
            converter = DoclingConverter()
            if converter.is_supported_format(file_path):
                conversion_options: dict[str, Any] = {"extract_images": extract_images}
                if output_path is not None:
                    conversion_options["output_file"] = output_path
                result = await asyncio.to_thread(
                    converter.convert_to_markdown,
                    file_path,
                    **conversion_options,
                )
                if result["success"]:
                    message = "\n   [INFO] Document converted to Markdown (Docling)"
                    message += f"\n   Markdown file: {result['output_file']}"
                    message += f"\n   Conversion time: {result['duration']:.2f} seconds"
                    if result.get("images_extracted", 0):
                        message += (
                            f"\n   Images extracted: {result['images_extracted']}"
                        )
                    return message
                warnings.append(f"Docling conversion failed: {result['error']}")
        except Exception as exc:
            warnings.append(f"Docling conversion error: {exc}")

    try:
        detected_kind = detect_document_kind(file_path)
    except DocumentConversionError as exc:
        detected_kind = None
        warnings.append(f"Could not identify document: {exc}")

    if detected_kind == "pdf" and PDF_FALLBACK_AVAILABLE:
        try:
            result = await asyncio.to_thread(
                SimplePdfConverter().convert_pdf_to_markdown,
                file_path,
                output_path,
            )
            if result["success"]:
                message = "\n   [INFO] PDF converted to Markdown (pypdf fallback)"
                message += f"\n   Markdown file: {result['output_file']}"
                message += f"\n   Conversion time: {result['duration']:.2f} seconds"
                message += f"\n   Pages extracted: {result['pages_extracted']}"
                return message
            warnings.append(f"PDF conversion failed: {result['error']}")
        except Exception as exc:
            warnings.append(f"PDF conversion error: {exc}")
    elif detected_kind is not None:
        started = datetime.now()
        try:
            result = await asyncio.to_thread(
                convert_to_markdown,
                file_path,
                output_path,
            )
            duration = (datetime.now() - started).total_seconds()
            message = "\n   [INFO] Document converted to Markdown (built-in)"
            message += f"\n   Markdown file: {result.output_file}"
            message += f"\n   Source format: {result.input_kind}"
            message += f"\n   Conversion time: {duration:.2f} seconds"
            message += f"\n   Characters extracted: {result.character_count}"
            return message
        except DocumentConversionError as exc:
            warnings.append(f"Built-in conversion failed: {exc}")
    return "\n   [WARNING] " + "; ".join(warnings) if warnings else None


async def convert_document_to_markdown(
    file_path: str,
    output_path: str | None = None,
    extract_images: bool = True,
) -> str:
    """Convert one local supported document through the shared conversion path."""

    parsed = urlparse(file_path)
    if parsed.scheme in {"http", "https"}:
        return format_error_message(
            "Conversion aborted",
            "download the URL with download_file_to before converting it",
        )
    source = os.path.abspath(os.path.expanduser(file_path))
    if not os.path.isfile(source):
        return format_error_message(
            "Conversion aborted",
            f"Input file not found: {source}",
        )
    destination = (
        os.path.abspath(os.path.expanduser(output_path)) if output_path else None
    )
    result = await perform_document_conversion(
        source,
        extract_images=extract_images,
        output_path=destination,
    )
    if not result or "[WARNING]" in result:
        detail = result.strip() if result else "no converter accepted the document"
        return format_error_message("Conversion failed", detail)
    return f"[SUCCESS] Document converted successfully!{result}"


def format_file_operation_result(
    operation: str,
    source: str,
    destination: str,
    result: dict[str, Any],
    conversion_msg: str | None = None,
) -> str:
    """
    格式化文件操作结果的共用逻辑

    Args:
        operation: 操作类型 ("download", "copy", 或 "move")
        source: 源文件/URL
        destination: 目标路径
        result: 操作结果字典
        conversion_msg: 转换消息

    Returns:
        格式化的结果消息
    """
    if result["success"]:
        size_mb = result["size"] / (1024 * 1024)

        # 处理不同操作类型的动词形式
        if operation == "copy":
            operation_verb = "copied"
        elif operation == "download":
            operation_verb = "downloaded"
        else:  # move
            operation_verb = "moved"

        msg = f"[SUCCESS] Successfully {operation_verb}: {source}\n"

        if operation == "download":
            msg += f"   File: {destination}\n"
            msg += f"   Size: {size_mb:.2f} MB\n"
            msg += f"   Time: {result['duration']:.2f} seconds\n"
            speed_mb = result.get("speed", 0) / (1024 * 1024)
            msg += f"   Speed: {speed_mb:.2f} MB/s"
        else:  # copy or move
            msg += f"   To: {destination}\n"
            msg += f"   Size: {size_mb:.2f} MB\n"
            msg += f"   Time: {result['duration']:.2f} seconds"
            if operation == "copy":
                msg += "\n   Note: Original file preserved"

        if conversion_msg:
            msg += conversion_msg

        return msg
    else:
        return f"[ERROR] Failed to {operation}: {source}\n   Error: {result.get('error', 'Unknown error')}"


class LocalPathExtractor:
    """本地路径提取器"""

    @staticmethod
    def is_local_path(path: str) -> bool:
        """判断是否为本地路径"""
        path = path.strip("\"'")

        # 检查是否为URL
        if re.match(r"^https?://", path, re.IGNORECASE) or re.match(
            r"^ftp://", path, re.IGNORECASE
        ):
            return False

        # 路径指示符
        path_indicators = [os.path.sep, "/", "\\", "~", ".", ".."]
        has_extension = bool(os.path.splitext(path)[1])

        if any(indicator in path for indicator in path_indicators) or has_extension:
            expanded_path = os.path.expanduser(path)
            return os.path.exists(expanded_path) or any(
                indicator in path for indicator in path_indicators
            )

        return False

    @staticmethod
    def extract_local_paths(text: str) -> list[str]:
        """从文本中提取本地文件路径"""
        patterns = [
            r'"([^"]+)"',
            r"'([^']+)'",
            r"(?:^|\s)((?:[~./\\]|[A-Za-z]:)?(?:[^/\\\s]+[/\\])*[^/\\\s]+\.[A-Za-z0-9]+)(?:\s|$)",
            r"(?:^|\s)((?:~|\.{1,2})?/[^\s]+)(?:\s|$)",
            r"(?:^|\s)([A-Za-z]:[/\\][^\s]+)(?:\s|$)",
            r"(?:^|\s)(\.{1,2}[/\\][^\s]+)(?:\s|$)",
        ]

        local_paths = []
        potential_paths = []

        for pattern in patterns:
            matches = re.findall(pattern, text, re.MULTILINE)
            potential_paths.extend(matches)

        for path in potential_paths:
            path = path.strip()
            if path and LocalPathExtractor.is_local_path(path):
                expanded_path = os.path.expanduser(path)
                if expanded_path not in local_paths:
                    local_paths.append(expanded_path)

        return local_paths


class URLExtractor:
    """URL提取器"""

    URL_PATTERNS = [
        r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+(?:/(?:[-\w._~!$&\'()*+,;=:@]|%[\da-fA-F]{2})*)*(?:\?(?:[-\w._~!$&\'()*+,;=:@/?]|%[\da-fA-F]{2})*)?(?:#(?:[-\w._~!$&\'()*+,;=:@/?]|%[\da-fA-F]{2})*)?",
        r"ftp://(?:[-\w.]|(?:%[\da-fA-F]{2}))+(?:/(?:[-\w._~!$&\'()*+,;=:@]|%[\da-fA-F]{2})*)*",
        r"(?<!\S)(?:www\.)?[-\w]+(?:\.[-\w]+)+/(?:[-\w._~!$&\'()*+,;=:@/]|%[\da-fA-F]{2})+",
    ]

    @staticmethod
    def convert_arxiv_url(url: str) -> str:
        """将arXiv网页链接转换为PDF下载链接"""
        # 匹配arXiv论文ID的正则表达式
        arxiv_pattern = r"arxiv\.org/abs/(\d+\.\d+)(?:v\d+)?"
        match = re.search(arxiv_pattern, url, re.IGNORECASE)
        if match:
            paper_id = match.group(1)
            return f"https://arxiv.org/pdf/{paper_id}.pdf"
        return url

    @classmethod
    def extract_urls(cls, text: str) -> list[str]:
        """从文本中提取URL"""
        urls = []

        # 首先处理特殊情况：@开头的URL
        at_url_pattern = r"@(https?://[^\s]+)"
        at_matches = re.findall(at_url_pattern, text, re.IGNORECASE)
        for match in at_matches:
            # 处理arXiv链接
            url = cls.convert_arxiv_url(match.rstrip("/"))
            urls.append(url)

        # 然后使用原有的正则模式
        for pattern in cls.URL_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                # 处理可能缺少协议的URL
                if not match.startswith(("http://", "https://", "ftp://")):
                    # 检查是否是 www 开头
                    if match.startswith("www."):
                        match = "https://" + match
                    else:
                        # 其他情况也添加 https
                        match = "https://" + match

                # 处理arXiv链接
                url = cls.convert_arxiv_url(match.rstrip("/"))
                urls.append(url)

        # 去重并保持顺序
        seen = set()
        unique_urls = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)

        return unique_urls

    @staticmethod
    def infer_filename_from_url(url: str) -> str:
        """从URL推断文件名"""
        parsed = urlparse(url)
        path = unquote(parsed.path)

        # 从路径中提取文件名
        filename = os.path.basename(path)

        # 特殊处理：arxiv PDF链接
        if "arxiv.org" in parsed.netloc and "/pdf/" in path:
            if filename:
                # 检查是否已经有合适的文件扩展名
                if not filename.lower().endswith((".pdf", ".doc", ".docx", ".txt")):
                    filename = f"{filename}.pdf"
            else:
                path_parts = [p for p in path.split("/") if p]
                if path_parts and path_parts[-1]:
                    filename = f"{path_parts[-1]}.pdf"
                else:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"arxiv_paper_{timestamp}.pdf"

        # 如果没有文件名或没有扩展名，生成一个
        elif not filename or "." not in filename:
            # 尝试从URL生成有意义的文件名
            domain = parsed.netloc.replace("www.", "").replace(".", "_")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # 尝试根据路径推断文件类型
            if not path or path == "/":
                filename = f"{domain}_{timestamp}.html"
            else:
                # 使用路径的最后一部分
                path_parts = [p for p in path.split("/") if p]
                if path_parts:
                    filename = f"{path_parts[-1]}_{timestamp}"
                else:
                    filename = f"{domain}_{timestamp}"

                # 如果还是没有扩展名，根据路径推断
                if "." not in filename:
                    # 根据路径中的关键词推断文件类型
                    if "/pdf/" in path.lower() or path.lower().endswith("pdf"):
                        filename += ".pdf"
                    elif any(
                        ext in path.lower() for ext in ["/doc/", "/word/", ".docx"]
                    ):
                        filename += ".docx"
                    elif any(
                        ext in path.lower()
                        for ext in ["/ppt/", "/powerpoint/", ".pptx"]
                    ):
                        filename += ".pptx"
                    elif any(ext in path.lower() for ext in ["/csv/", ".csv"]):
                        filename += ".csv"
                    elif any(ext in path.lower() for ext in ["/zip/", ".zip"]):
                        filename += ".zip"
                    else:
                        filename += ".html"

        return filename


class PathExtractor:
    """路径提取器"""

    @staticmethod
    def extract_target_path(text: str) -> str | None:
        """从文本中提取目标路径"""
        patterns = [
            r'(?:save|download|store|put|place|write|copy|move)\s+(?:to|into|in|at)\s+["\']?([^\s"\']+)["\']?',
            r'(?:to|into|in|at)\s+(?:folder|directory|dir|path|location)\s*["\']?([^\s"\']+)["\']?',
            r'(?:destination|target|output)\s*(?:is|:)?\s*["\']?([^\s"\']+)["\']?',
            r'(?:保存|下载|存储|放到|写入|复制|移动)(?:到|至|去)\s*["\']?([^\s"\']+)["\']?',
            r'(?:到|在|至)\s*["\']?([^\s"\']+)["\']?\s*(?:文件夹|目录|路径|位置)',
        ]

        filter_words = {
            "here",
            "there",
            "current",
            "local",
            "this",
            "that",
            "这里",
            "那里",
            "当前",
            "本地",
            "这个",
            "那个",
        }

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                path = match.group(1).strip("。，,.、")
                if path and path.lower() not in filter_words:
                    return path

        return None


class SimplePdfConverter:
    """简单的PDF转换器，使用 pypdf 提取文本"""

    def convert_pdf_to_markdown(
        self, input_file: str, output_file: str | None = None
    ) -> dict[str, Any]:
        """
        使用 pypdf 将 PDF 转换为 Markdown 格式

        Args:
            input_file: 输入PDF文件路径
            output_file: 输出Markdown文件路径（可选）

        Returns:
            转换结果字典
        """
        if not PDF_FALLBACK_AVAILABLE:
            return {"success": False, "error": "pypdf is not available"}

        try:
            # 检查输入文件是否存在
            if not os.path.exists(input_file):
                return {
                    "success": False,
                    "error": f"Input file not found: {input_file}",
                }

            # 如果没有指定输出文件，自动生成
            if not output_file:
                base_name = os.path.splitext(input_file)[0]
                output_file = f"{base_name}.md"

            # 确保输出目录存在
            output_dir = os.path.dirname(output_file)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)

            # 执行转换
            start_time = datetime.now()

            # 读取PDF文件
            with open(input_file, "rb") as file:
                pdf_reader = pypdf.PdfReader(file)
                text_content = []

                # 提取每页文本
                for page_num, page in enumerate(pdf_reader.pages, 1):
                    text = page.extract_text()
                    if text.strip():
                        text_content.append(f"## Page {page_num}\n\n{text.strip()}\n\n")

            # 生成Markdown内容
            markdown_content = f"# Extracted from {os.path.basename(input_file)}\n\n"
            markdown_content += f"*Total pages: {len(pdf_reader.pages)}*\n\n"
            markdown_content += "---\n\n"
            markdown_content += "".join(text_content)

            # 保存到文件
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(markdown_content)

            # 计算转换时间
            duration = (datetime.now() - start_time).total_seconds()

            # 获取文件大小
            input_size = os.path.getsize(input_file)
            output_size = os.path.getsize(output_file)

            return {
                "success": True,
                "input_file": input_file,
                "output_file": output_file,
                "input_size": input_size,
                "output_size": output_size,
                "duration": duration,
                "markdown_content": markdown_content,
                "pages_extracted": len(pdf_reader.pages),
            }

        except Exception as e:
            return {
                "success": False,
                "input_file": input_file,
                "error": f"Conversion failed: {e!s}",
            }


class DoclingConverter:
    """文档转换器，使用docling将文档转换为Markdown格式，支持图片提取"""

    def __init__(self):
        if not DOCLING_AVAILABLE:
            raise ImportError(
                "docling package is not available. Please install it first."
            )

        # 配置PDF处理选项
        pdf_pipeline_options = PdfPipelineOptions()
        pdf_pipeline_options.do_ocr = False  # 暂时禁用OCR以避免认证问题
        pdf_pipeline_options.do_table_structure = False  # 暂时禁用表格结构识别

        # 创建文档转换器（使用基础模式）
        try:
            self.converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=pdf_pipeline_options
                    )
                }
            )
        except Exception:
            # 如果失败，尝试更简单的配置
            self.converter = DocumentConverter()

    def is_supported_format(self, file_path: str) -> bool:
        """检查文件格式是否支持转换"""
        if not DOCLING_AVAILABLE:
            return False

        supported_extensions = {".pdf", ".docx", ".pptx", ".html", ".md", ".txt"}
        file_extension = os.path.splitext(file_path)[1].lower()
        return file_extension in supported_extensions

    def is_url(self, path: str) -> bool:
        """检查路径是否为URL"""
        try:
            result = urlparse(path)
            return result.scheme in ("http", "https")
        except Exception:
            return False

    def extract_images(self, doc, output_dir: str) -> dict[str, str]:
        """
        提取文档中的图片并保存到本地

        Args:
            doc: docling文档对象
            output_dir: 输出目录

        Returns:
            图片ID到本地文件路径的映射
        """
        images_dir = os.path.join(output_dir, "images")
        os.makedirs(images_dir, exist_ok=True)
        image_map = {}  # docling图片id -> 本地文件名

        try:
            # 获取文档中的图片
            images = getattr(doc, "images", [])

            for idx, img in enumerate(images):
                try:
                    # 获取图片格式，默认为png
                    ext = getattr(img, "format", None) or "png"
                    if ext.lower() not in ["png", "jpg", "jpeg", "gif", "bmp", "webp"]:
                        ext = "png"

                    # 生成文件名
                    filename = f"image_{idx + 1}.{ext}"
                    filepath = os.path.join(images_dir, filename)

                    # 保存图片数据
                    img_data = getattr(img, "data", None)
                    if img_data:
                        with open(filepath, "wb") as f:
                            f.write(img_data)

                        # 计算相对路径
                        rel_path = os.path.relpath(filepath, output_dir)
                        img_id = getattr(img, "id", str(idx + 1))
                        image_map[img_id] = rel_path

                except Exception as img_error:
                    print(
                        f"Warning: Failed to extract image {idx + 1}: {img_error}",
                        file=sys.stderr,
                    )
                    continue

        except Exception as e:
            print(f"Warning: Failed to extract images: {e}", file=sys.stderr)

        return image_map

    def process_markdown_with_images(
        self, markdown_content: str, image_map: dict[str, str]
    ) -> str:
        """
        处理Markdown内容，替换图片占位符为实际的图片路径

        Args:
            markdown_content: 原始Markdown内容
            image_map: 图片ID到本地路径的映射

        Returns:
            处理后的Markdown内容
        """

        def replace_img(match):
            img_id = match.group(1)
            if img_id in image_map:
                return f"![Image]({image_map[img_id]})"
            else:
                return match.group(0)

        # 替换docling的图片占位符
        processed_content = re.sub(
            r"!\[Image\]\(docling://image/([^)]+)\)", replace_img, markdown_content
        )

        return processed_content

    def convert_to_markdown(
        self,
        input_file: str,
        output_file: str | None = None,
        extract_images: bool = True,
    ) -> dict[str, Any]:
        """
        将文档转换为Markdown格式，支持图片提取

        Args:
            input_file: 输入文件路径或URL
            output_file: 输出Markdown文件路径（可选）
            extract_images: 是否提取图片（默认True）

        Returns:
            转换结果字典
        """
        if not DOCLING_AVAILABLE:
            return {"success": False, "error": "docling package is not available"}

        try:
            # 检查输入文件（如果不是URL）
            if not self.is_url(input_file):
                if not os.path.exists(input_file):
                    return {
                        "success": False,
                        "error": f"Input file not found: {input_file}",
                    }

                # 检查文件格式是否支持
                if not self.is_supported_format(input_file):
                    return {
                        "success": False,
                        "error": f"Unsupported file format: {os.path.splitext(input_file)[1]}",
                    }
            else:
                # 对于URL，检查是否为支持的格式
                if not input_file.lower().endswith(
                    (".pdf", ".docx", ".pptx", ".html", ".md", ".txt")
                ):
                    return {
                        "success": False,
                        "error": f"Unsupported URL format: {input_file}",
                    }

            # 如果没有指定输出文件，自动生成
            if not output_file:
                if self.is_url(input_file):
                    # 从URL生成文件名
                    filename = URLExtractor.infer_filename_from_url(input_file)
                    base_name = os.path.splitext(filename)[0]
                else:
                    base_name = os.path.splitext(input_file)[0]
                output_file = f"{base_name}.md"

            # 确保输出目录存在
            output_dir = os.path.dirname(output_file) or "."
            os.makedirs(output_dir, exist_ok=True)

            # 执行转换
            start_time = datetime.now()
            result = self.converter.convert(input_file)
            doc = result.document

            # 提取图片（如果启用）
            image_map = {}
            images_extracted = 0
            if extract_images:
                image_map = self.extract_images(doc, output_dir)
                images_extracted = len(image_map)

            # 获取Markdown内容
            markdown_content = doc.export_to_markdown()

            # 处理图片占位符
            if extract_images and image_map:
                markdown_content = self.process_markdown_with_images(
                    markdown_content, image_map
                )

            # 保存到文件
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(markdown_content)

            # 计算转换时间
            duration = (datetime.now() - start_time).total_seconds()

            # 获取文件大小
            if self.is_url(input_file):
                input_size = 0  # URL无法直接获取大小
            else:
                input_size = os.path.getsize(input_file)
            output_size = os.path.getsize(output_file)

            return {
                "success": True,
                "input_file": input_file,
                "output_file": output_file,
                "input_size": input_size,
                "output_size": output_size,
                "duration": duration,
                "markdown_content": markdown_content,
                "images_extracted": images_extracted,
                "image_map": image_map,
            }

        except Exception as e:
            return {
                "success": False,
                "input_file": input_file,
                "error": f"Conversion failed: {e!s}",
            }


async def check_url_accessible(url: str) -> dict[str, Any]:
    """检查URL是否可访问"""
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        connector = aiohttp.TCPConnector(resolver=_SafeResolver(), use_dns_cache=False)
        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector
        ) as session:
            response = await _request_with_safe_redirects(session, "HEAD", url)
            async with response:
                raw_length = response.headers.get("Content-Length", "0")
                content_length = int(raw_length) if raw_length.isdigit() else 0
                allowed_size = content_length <= _MAX_DOWNLOAD_BYTES
                return {
                    "accessible": response.status < 400 and allowed_size,
                    "status": response.status if allowed_size else 413,
                    "content_type": response.headers.get("Content-Type", ""),
                    "content_length": content_length,
                }
    except (aiohttp.ClientError, OSError, ValueError):
        return {
            "accessible": False,
            "status": 0,
            "content_type": "",
            "content_length": 0,
        }


async def download_file(url: str, destination: str) -> dict[str, Any]:
    """下载单个文件"""
    start_time = datetime.now()
    chunk_size = 8192

    try:
        timeout = aiohttp.ClientTimeout(total=300)  # 5分钟超时
        connector = aiohttp.TCPConnector(resolver=_SafeResolver(), use_dns_cache=False)
        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector
        ) as session:
            response = await _request_with_safe_redirects(session, "GET", url)
            async with response:
                # 检查响应状态
                response.raise_for_status()

                raw_length = response.headers.get("Content-Length", "0")
                content_length = int(raw_length) if raw_length.isdigit() else 0
                if content_length > _MAX_DOWNLOAD_BYTES:
                    raise ValueError(
                        f"download exceeds {_MAX_DOWNLOAD_BYTES // (1024 * 1024)} MiB limit"
                    )

                # 获取文件信息
                content_type = response.headers.get(
                    "Content-Type", "application/octet-stream"
                )

                # 确保目标目录存在
                parent_dir = os.path.dirname(destination)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)

                # 下载文件
                downloaded = 0
                partial_path = f"{destination}.part"
                try:
                    async with aiofiles.open(partial_path, "wb") as file:
                        async for chunk in response.content.iter_chunked(chunk_size):
                            downloaded += len(chunk)
                            if downloaded > _MAX_DOWNLOAD_BYTES:
                                raise ValueError(
                                    "download exceeded the configured size limit"
                                )
                            await file.write(chunk)
                    os.replace(partial_path, destination)
                except BaseException:
                    try:
                        os.remove(partial_path)
                    except FileNotFoundError:
                        pass
                    raise

                # 计算下载时间
                duration = (datetime.now() - start_time).total_seconds()

                return {
                    "success": True,
                    "url": url,
                    "destination": destination,
                    "size": downloaded,
                    "content_type": content_type,
                    "duration": duration,
                    "speed": downloaded / duration if duration > 0 else 0,
                }

    except aiohttp.ClientError as e:
        return {
            "success": False,
            "url": url,
            "destination": destination,
            "error": f"Network error: {e!s}",
        }
    except Exception as e:
        return {
            "success": False,
            "url": url,
            "destination": destination,
            "error": f"Download error: {e!s}",
        }


async def move_local_file(source_path: str, destination: str) -> dict[str, Any]:
    """复制本地文件到目标位置（保留原文件）"""
    start_time = datetime.now()

    try:
        # 检查源文件是否存在
        if not os.path.exists(source_path):
            return {
                "success": False,
                "source": source_path,
                "destination": destination,
                "error": f"Source file not found: {source_path}",
            }

        # 获取源文件信息
        source_size = os.path.getsize(source_path)

        # 确保目标目录存在
        parent_dir = os.path.dirname(destination)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        # 执行复制操作（保留原文件，防止数据丢失）
        shutil.copy2(source_path, destination)

        # 计算操作时间
        duration = (datetime.now() - start_time).total_seconds()

        return {
            "success": True,
            "source": source_path,
            "destination": destination,
            "size": source_size,
            "duration": duration,
            "operation": "copy",  # 改为copy
        }

    except Exception as e:
        return {
            "success": False,
            "source": source_path,
            "destination": destination,
            "error": f"Copy error: {e!s}",
        }


async def download_files(instruction: str) -> str:
    """
    Download files from URLs or move local files mentioned in natural language instructions.

    Args:
        instruction: Natural language instruction containing URLs/local paths and optional destination paths

    Returns:
        Status message about the download/move operations

    Examples:
        - "Download https://example.com/file.pdf to documents folder"
        - "Move /home/user/file.pdf to documents folder"
        - "Please get https://raw.githubusercontent.com/user/repo/main/data.csv and save it to ~/downloads"
        - "移动 ~/Desktop/report.docx 到 /tmp/documents/"
        - "Download www.example.com/report.xlsx"
    """
    urls = URLExtractor.extract_urls(instruction)
    local_paths = LocalPathExtractor.extract_local_paths(instruction)

    if not urls and not local_paths:
        return format_error_message(
            "Failed to parse instruction",
            "No downloadable URLs or movable local files found",
        )

    target_path = PathExtractor.extract_target_path(instruction)

    # 处理文件
    results = []

    # 处理URL下载
    for url in urls:
        try:
            # 推断文件名
            filename = URLExtractor.infer_filename_from_url(url)

            # 构建完整的目标路径
            if target_path:
                # 处理路径
                if target_path.startswith("~"):
                    target_path = os.path.expanduser(target_path)

                # 确保使用相对路径（如果不是绝对路径）
                if not os.path.isabs(target_path):
                    target_path = os.path.normpath(target_path)

                # 判断是文件路径还是目录路径
                if os.path.splitext(target_path)[1]:  # 有扩展名，是文件
                    destination = target_path
                else:  # 是目录
                    destination = os.path.join(target_path, filename)
            else:
                # 默认下载到当前目录
                destination = filename

            # 检查文件是否已存在
            if os.path.exists(destination):
                results.append(
                    f"[WARNING] Skipped {url}: File already exists at {destination}"
                )
                continue

            # 先检查URL是否可访问
            check_result = await check_url_accessible(url)
            if not check_result["accessible"]:
                results.append(
                    f"[ERROR] Failed to access {url}: HTTP {check_result['status'] or 'Connection failed'}"
                )
                continue

            # 执行下载
            result = await download_file(url, destination)

            # 执行转换（如果成功下载）
            conversion_msg = None
            if result["success"]:
                conversion_msg = await perform_document_conversion(
                    destination, extract_images=True
                )

            # 格式化结果
            msg = format_file_operation_result(
                "download", url, destination, result, conversion_msg
            )

        except Exception as e:
            msg = f"[ERROR] Failed to download: {url}\n"
            msg += f"   Error: {e!s}"

        results.append(msg)

    # 处理本地文件移动
    for local_path in local_paths:
        try:
            # 获取文件名
            filename = os.path.basename(local_path)

            # 构建完整的目标路径
            if target_path:
                # 处理路径
                if target_path.startswith("~"):
                    target_path = os.path.expanduser(target_path)

                # 确保使用相对路径（如果不是绝对路径）
                if not os.path.isabs(target_path):
                    target_path = os.path.normpath(target_path)

                # 判断是文件路径还是目录路径
                if os.path.splitext(target_path)[1]:  # 有扩展名，是文件
                    destination = target_path
                else:  # 是目录
                    destination = os.path.join(target_path, filename)
            else:
                # 默认移动到当前目录
                destination = filename

            # 检查目标文件是否已存在
            if os.path.exists(destination):
                results.append(
                    f"[WARNING] Skipped {local_path}: File already exists at {destination}"
                )
                continue

            # 执行复制（保留原文件）
            result = await move_local_file(local_path, destination)

            # 执行转换（如果成功复制）
            conversion_msg = None
            if result["success"]:
                conversion_msg = await perform_document_conversion(
                    destination, extract_images=True
                )

            # 格式化结果
            msg = format_file_operation_result(
                "copy", local_path, destination, result, conversion_msg
            )

        except Exception as e:
            msg = f"[ERROR] Failed to copy: {local_path}\n"
            msg += f"   Error: {e!s}"

        results.append(msg)

    return "\n\n".join(results)


async def parse_download_urls(text: str) -> str:
    """
    Extract URLs, local paths and target paths from text without downloading or moving.

    Args:
        text: Text containing URLs, local paths and optional destination paths

    Returns:
        Parsed URLs, local paths and target path information
    """
    urls = URLExtractor.extract_urls(text)
    local_paths = LocalPathExtractor.extract_local_paths(text)
    target_path = PathExtractor.extract_target_path(text)

    content = "📋 Parsed file operation information:\n\n"

    if urls:
        content += f"🔗 URLs found ({len(urls)}):\n"
        for i, url in enumerate(urls, 1):
            filename = URLExtractor.infer_filename_from_url(url)
            content += f"  {i}. {url}\n     📄 Filename: {filename}\n"
    else:
        content += "🔗 No URLs found\n"

    if local_paths:
        content += f"\n📁 Local files found ({len(local_paths)}):\n"
        for i, path in enumerate(local_paths, 1):
            exists = os.path.exists(path)
            content += f"  {i}. {path}\n"
            content += f"     ✅ Exists: {'Yes' if exists else 'No'}\n"
            if exists:
                size_mb = os.path.getsize(path) / (1024 * 1024)
                content += f"     📊 Size: {size_mb:.2f} MB\n"
    else:
        content += "\n📁 No local files found\n"

    if target_path:
        content += f"\n🎯 Target path: {target_path}"
        if target_path.startswith("~"):
            content += f"\n   (Expanded: {os.path.expanduser(target_path)})"
    else:
        content += "\n🎯 Target path: Not specified (will use current directory)"

    return content


async def download_file_to(
    url: str, destination: str | None = None, filename: str | None = None
) -> str:
    """
    Download a specific file with detailed options.

    Args:
        url: URL to download from
        destination: Target directory or full file path (optional)
        filename: Specific filename to use (optional, ignored if destination is a full file path)

    Returns:
        Status message about the download operation
    """
    # 确定文件名

    url = URLExtractor.extract_urls(url)[0]

    if not filename:
        filename = URLExtractor.infer_filename_from_url(url)

    if not filename:
        filename = URLExtractor.infer_filename_from_url(url)
    else:
        name_source, extension_source = os.path.splitext(
            os.path.basename(URLExtractor.infer_filename_from_url(url))
        )
        name_destination, extension_destination = os.path.splitext(
            os.path.basename(filename)
        )
        if extension_source:
            filename = name_destination + extension_source
        else:
            filename = name_destination + extension_destination

    # 确定完整路径
    if destination:
        # 展开用户目录
        if destination.startswith("~"):
            destination = os.path.expanduser(destination)

        # 检查是否是完整文件路径
        if os.path.splitext(destination)[1]:  # 有扩展名
            target_path = destination
        else:  # 是目录
            target_path = os.path.join(destination, filename)
    else:
        target_path = filename

    # 确保使用相对路径（如果不是绝对路径）
    if not os.path.isabs(target_path):
        target_path = os.path.normpath(target_path)

    # 检查文件是否已存在
    if os.path.exists(target_path):
        return format_error_message(
            "Download aborted", f"File already exists at {target_path}"
        )

    # 先检查URL
    check_result = await check_url_accessible(url)
    if not check_result["accessible"]:
        return format_error_message(
            "Cannot access URL",
            f"{url} (HTTP {check_result['status'] or 'Connection failed'})",
        )

    # 显示下载信息
    size_mb = (
        int(check_result["content_length"]) / (1024 * 1024)
        if check_result["content_length"]
        else 0
    )
    msg = "[INFO] Downloading file:\n"
    msg += f"   URL: {url}\n"
    msg += f"   Target: {target_path}\n"
    if size_mb > 0:
        msg += f"   Expected size: {size_mb:.2f} MB\n"
    msg += "\n"

    # 执行下载
    result = await download_file(url, target_path)

    # 执行转换（如果成功下载）
    conversion_msg = None
    if result["success"]:
        conversion_msg = await perform_document_conversion(
            target_path, extract_images=True
        )

        # 添加下载信息前缀
        actual_size_mb = result["size"] / (1024 * 1024)
        speed_mb = result["speed"] / (1024 * 1024)
        info_msg = "[SUCCESS] Download completed!\n"
        info_msg += f"   Saved to: {target_path}\n"
        info_msg += f"   Size: {actual_size_mb:.2f} MB\n"
        info_msg += f"   Duration: {result['duration']:.2f} seconds\n"
        info_msg += f"   Speed: {speed_mb:.2f} MB/s\n"
        info_msg += f"   Type: {result['content_type']}"

        if conversion_msg:
            info_msg += conversion_msg

        return msg + info_msg
    else:
        return msg + f"[ERROR] Download failed!\n   Error: {result['error']}"


async def move_file_to(
    source: str, destination: str | None = None, filename: str | None = None
) -> str:
    """
    Copy a local file to a new location (preserves original file).

    Note: Despite the name "move_file_to", this tool COPIES the file to preserve the original.
    This prevents data loss during file processing workflows.

    Args:
        source: Source file path to copy
        destination: Target directory or full file path (optional)
        filename: Specific filename to use (optional, ignored if destination is a full file path)

    Returns:
        Status message about the copy operation
    """
    # 展开源路径
    if source.startswith("~"):
        source = os.path.expanduser(source)

    # 检查源文件是否存在
    if not os.path.exists(source):
        return format_error_message("Copy aborted", f"Source file not found: {source}")

    # 确定文件名
    if not filename:
        filename = os.path.basename(source)
    else:
        name_source, extension_source = os.path.splitext(os.path.basename(source))
        name_destination, extension_destination = os.path.splitext(
            os.path.basename(filename)
        )
        if extension_source:
            filename = name_destination + extension_source
        else:
            filename = name_destination + extension_destination

    # 确定完整路径
    if destination:
        # 展开用户目录
        if destination.startswith("~"):
            destination = os.path.expanduser(destination)

        # 检查是否是完整文件路径
        if os.path.splitext(destination)[1]:  # 有扩展名
            target_path = destination
        else:  # 是目录
            target_path = os.path.join(destination, filename)

    else:
        target_path = filename

    # 确保使用相对路径（如果不是绝对路径）
    if not os.path.isabs(target_path):
        target_path = os.path.normpath(target_path)

    # 检查目标文件是否已存在
    if os.path.exists(target_path):
        return f"[ERROR] Target file already exists: {target_path}"

    # 显示复制信息
    source_size_mb = os.path.getsize(source) / (1024 * 1024)
    msg = "[INFO] Copying file (original preserved):\n"
    msg += f"   Source: {source}\n"
    msg += f"   Target: {target_path}\n"
    msg += f"   Size: {source_size_mb:.2f} MB\n"
    msg += "\n"

    # 执行复制（保留原文件）
    result = await move_local_file(source, target_path)

    # 执行转换（如果成功复制）
    conversion_msg = None
    if result["success"]:
        conversion_msg = await perform_document_conversion(
            target_path, extract_images=True
        )

        # 添加复制信息前缀
        info_msg = "[SUCCESS] File copied successfully (original preserved)!\n"
        info_msg += f"   From: {source}\n"
        info_msg += f"   To: {target_path}\n"
        info_msg += f"   Duration: {result['duration']:.2f} seconds"

        if conversion_msg:
            info_msg += conversion_msg

        return msg + info_msg
    else:
        return msg + f"[ERROR] Copy failed!\n   Error: {result['error']}"


if __name__ == "__main__":
    import_module("tools.pdf_downloader_server").run()
