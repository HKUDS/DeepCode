"""Web tools: web_fetch, web_search."""

import html
import json
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from nanobot.agent.tools.base import Tool

# Shared constants
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
MAX_REDIRECTS = 5  # Limit redirects to prevent DoS attacks


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _normalize(text: str) -> str:
    """Normalize whitespace."""
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _validate_url(url: str) -> tuple[bool, str]:
    """Validate URL: must be http(s) with valid domain."""
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False, f"Only http/https allowed, got '{p.scheme or 'none'}'"
        if not p.netloc:
            return False, "Missing domain"
        return True, ""
    except Exception as e:
        return False, str(e)


def _get_metaso_api_key() -> str:
    """Get Metaso API key from env var METASO_API_KEY, or fall back to the
    built-in default key which has a free quota of ~100 searches/day.
    Set your own key to raise that limit."""
    return os.environ.get("METASO_API_KEY", "mk-E384C1DD5E8501BB7EFE27C949AFDE5B")


class WebFetchTool(Tool):
    """Fetch and extract content from a URL using Readability."""

    name = "web_fetch"
    description = "Fetch URL and extract readable content (HTML → markdown/text)."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "extractMode": {"type": "string", "enum": ["markdown", "text"], "default": "markdown"},
            "maxChars": {"type": "integer", "minimum": 100},
        },
        "required": ["url"],
    }

    def __init__(self, max_chars: int = 50000):
        self.max_chars = max_chars

    async def execute(
        self,
        url: str,
        extract_mode: str = "markdown",
        max_chars: int | None = None,
        **kwargs: Any,
    ) -> str:
        from readability import Document

        # Backward compatibility for callers using camelCase argument names
        if "extractMode" in kwargs and extract_mode == "markdown":
            extract_mode = kwargs["extractMode"]
        if "maxChars" in kwargs and max_chars is None:
            max_chars = kwargs["maxChars"]

        max_chars = max_chars or self.max_chars

        # Validate URL before fetching
        is_valid, error_msg = _validate_url(url)
        if not is_valid:
            return json.dumps({"error": f"URL validation failed: {error_msg}", "url": url})

        try:
            async with httpx.AsyncClient(
                follow_redirects=True, max_redirects=MAX_REDIRECTS, timeout=30.0
            ) as client:
                r = await client.get(url, headers={"User-Agent": USER_AGENT})
                r.raise_for_status()

            ctype = r.headers.get("content-type", "")

            # JSON
            if "application/json" in ctype:
                text, extractor = json.dumps(r.json(), indent=2), "json"
            # HTML
            elif "text/html" in ctype or r.text[:256].lower().startswith(("<!doctype", "<html")):
                doc = Document(r.text)
                content = (
                    self._to_markdown(doc.summary())
                    if extract_mode == "markdown"
                    else _strip_tags(doc.summary())
                )
                text = f"# {doc.title()}\n\n{content}" if doc.title() else content
                extractor = "readability"
            else:
                text, extractor = r.text, "raw"

            truncated = len(text) > max_chars
            if truncated:
                text = text[:max_chars]

            return json.dumps(
                {
                    "url": url,
                    "finalUrl": str(r.url),
                    "status": r.status_code,
                    "extractor": extractor,
                    "truncated": truncated,
                    "length": len(text),
                    "text": text,
                }
            )
        except Exception as e:
            return json.dumps({"error": str(e), "url": url})

    def _to_markdown(self, html: str) -> str:
        """Convert HTML to markdown."""
        # Convert links, headings, lists before stripping tags
        text = re.sub(
            r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
            lambda m: f"[{_strip_tags(m[2])}]({m[1]})",
            html,
            flags=re.I,
        )
        text = re.sub(
            r"<h([1-6])[^>]*>([\s\S]*?)</h\1>",
            lambda m: f'\n{"#" * int(m[1])} {_strip_tags(m[2])}\n',
            text,
            flags=re.I,
        )
        text = re.sub(
            r"<li[^>]*>([\s\S]*?)</li>", lambda m: f"\n- {_strip_tags(m[1])}", text, flags=re.I
        )
        text = re.sub(r"</(p|div|section|article)>", "\n\n", text, flags=re.I)
        text = re.sub(r"<(br|hr)\s*/?>", "\n", text, flags=re.I)
        return _normalize(_strip_tags(text))


class WebSearchTool(Tool):
    """Search the web using the Metaso API."""

    name = "web_search"
    description = (
        "Search the web for information. Returns a list of results "
        "with titles, URLs, and snippets. Useful for finding current "
        "information, looking up facts, or researching topics."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query string",
            },
            "topK": {
                "type": "integer",
                "description": "Maximum number of results to return (1-100). Default: 10",
                "minimum": 1,
                "maximum": 100,
            },
        },
        "required": ["query"],
    }

    _METASO_URL = "https://metaso.cn/api/v1/search"
    _DEFAULT_TOP_K = 10
    _REQUEST_TIMEOUT = 30.0

    async def execute(
        self,
        query: str,
        top_k: int | None = None,
        **kwargs: Any,
    ) -> str:
        if "topK" in kwargs and top_k is None:
            top_k = kwargs["topK"]

        top_k = max(1, min(top_k or self._DEFAULT_TOP_K, 100))
        api_key = _get_metaso_api_key()

        try:
            async with httpx.AsyncClient(
                timeout=self._REQUEST_TIMEOUT,
                follow_redirects=True,
                max_redirects=MAX_REDIRECTS,
            ) as client:
                resp = await client.post(
                    self._METASO_URL,
                    json={"q": query, "scope": "webpage", "size": top_k},
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )

            if resp.status_code in (401, 403):
                return json.dumps(
                    {
                        "error": "Metaso API unauthorized. Check METASO_API_KEY.",
                        "query": query,
                    }
                )

            if resp.status_code == 429:
                return json.dumps(
                    {
                        "error": "Metaso API rate limited. Please retry later.",
                        "query": query,
                    }
                )

            resp.raise_for_status()
            data = resp.json()

            code = data.get("code")
            if code == 3003:
                return json.dumps({"error": "Metaso daily search limit reached.", "query": query})
            if code == 2005:
                return json.dumps(
                    {
                        "error": "Metaso API unauthorized (error 2005). Check METASO_API_KEY.",
                        "query": query,
                    }
                )
            if code and code != 0:
                return json.dumps(
                    {
                        "error": f"Metaso API error {code}: {data.get('message', 'unknown')}",
                        "query": query,
                    }
                )

            webpages = data.get("webpages", [])
            results = [
                {
                    "title": wp.get("title", ""),
                    "url": wp.get("link", ""),
                    "snippet": wp.get("snippet", "") or wp.get("summary", ""),
                }
                for wp in webpages
            ]

            return json.dumps(
                {
                    "query": query,
                    "total": data.get("total", len(results)),
                    "results": results,
                }
            )

        except httpx.ConnectError:
            return json.dumps(
                {"error": "Cannot connect to Metaso API (metaso.cn).", "query": query}
            )
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {
                    "error": f"Metaso API HTTP error: {e.response.status_code}",
                    "query": query,
                    "detail": e.response.text[:500],
                }
            )
        except Exception as e:
            return json.dumps({"error": str(e), "query": query})
