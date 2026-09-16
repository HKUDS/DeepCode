"""Same-origin browser assets and bounded, authenticated workspace transfers."""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import re
import secrets
import stat
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

from app_server.browser_auth import BrowserAuth
from core.application.errors import ApplicationError
from core.private_storage import ensure_private_file
from core.version import __version__

ASSET_DIRECTORY = Path(__file__).with_name("web_assets")
MAX_UPLOAD = 10 * 1024 * 1024
MAX_DOWNLOAD = 32 * 1024 * 1024


def read_web_build(assets: Path = ASSET_DIRECTORY) -> dict | None:
    try:
        value = json.loads((assets / "web-build.json").read_text())
        if value.get("version") != __version__ or not isinstance(
            value.get("buildId"), str
        ):
            return None
        return value
    except (OSError, ValueError, AttributeError):
        return None


async def _file_io(function, *args):
    """Finish the current descriptor operation before cancellation closes it."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class WebSurface:
    def __init__(
        self, application, auth: BrowserAuth, phase, *, assets: Path = ASSET_DIRECTORY
    ):
        self.application = application
        self.auth = auth
        self.phase = phase
        self.assets = assets.resolve()
        self._uploads = asyncio.Semaphore(4)
        self._upload_lock = asyncio.Lock()

    def build(self) -> dict | None:
        return read_web_build(self.assets)

    def routes(self):
        return [
            web.get("/", self.index),
            web.get("/index.html", self.index),
            web.get("/web-build.json", self.manifest),
            web.get("/assets/{path:.*}", self.asset),
            web.get("/api/session", self.session),
            web.post("/api/uploads", self.upload),
            web.get("/api/download", self.download),
        ]

    async def index(self, request):
        if self.build() is None or not (self.assets / "index.html").is_file():
            return web.Response(
                status=503,
                text="DeepCode web assets are missing or incompatible. Reinstall a complete release, or run npm ci and npm run build:web in desktop/ before starting this service.",
            )
        return web.FileResponse(self.assets / "index.html")

    async def manifest(self, request):
        build = self.build()
        if build is None:
            raise web.HTTPServiceUnavailable(text="Web assets are unavailable")
        return web.json_response(build)

    async def asset(self, request):
        relative = Path(request.match_info["path"])
        path = (self.assets / "assets" / relative).resolve()
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not path.is_relative_to(self.assets / "assets")
            or not path.is_file()
        ):
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def session(self, request):
        self.auth.require(request)
        return web.json_response(
            {
                "authenticated": True,
                "phase": self.phase(),
                "version": __version__,
                "webBuild": self.build(),
            }
        )

    async def upload(self, request):
        self.auth.require(request)
        if self.phase() != "ready":
            raise web.HTTPServiceUnavailable(text="Service is not accepting uploads")
        if request.content_type != "application/octet-stream":
            raise web.HTTPUnsupportedMediaType(text="Binary upload body required")
        if request.content_length is not None and request.content_length > MAX_UPLOAD:
            raise web.HTTPRequestEntityTooLarge(
                max_size=MAX_UPLOAD, actual_size=request.content_length
            )
        try:
            context = await asyncio.to_thread(
                self.application.workspaces.resolve,
                request.query.get("threadId", ""),
                require_trusted=True,
            )
        except ApplicationError as exc:
            raise web.HTTPForbidden(text=exc.user_message) from exc
        # A new root-level file avoids following user-controlled subdirectories
        # or accepting a browser-local path as a server filesystem path.
        name = (
            re.sub(
                r"[^\w.\-]", "_", Path(request.query.get("name", "attachment")).name
            )[:100]
            or "attachment"
        )
        filename = f"deepcode-upload-{secrets.token_hex(12)}-{name}"
        async with self._uploads, self._upload_lock:
            directory = (
                os.open(context.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                if os.open in os.supports_dir_fd
                else None
            )
            staging_name = f".{filename}.part"
            target = (
                staging_name if directory is not None else context.root / staging_name
            )
            destination = filename if directory is not None else context.root / filename
            descriptor = None
            completed = False
            try:
                existing = await asyncio.to_thread(
                    lambda: sum(
                        path.stat().st_size
                        for path in itertools.chain(
                            context.root.glob("deepcode-upload-*"),
                            context.root.glob(".deepcode-upload-*.part"),
                        )
                        if path.is_file() and not path.is_symlink()
                    )
                )
                if existing >= 64 * 1024 * 1024:
                    raise web.HTTPConflict(
                        text="Remove unused uploaded files before adding more (64 MiB workspace upload budget)"
                    )
                descriptor = os.open(
                    target,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_BINARY", 0),
                    0o600,
                    dir_fd=directory,
                )
                if os.name == "nt":
                    await _file_io(ensure_private_file, context.root / staging_name)
                count = 0
                async for chunk in request.content.iter_chunked(64 * 1024):
                    count += len(chunk)
                    if count > MAX_UPLOAD or existing + count > 64 * 1024 * 1024:
                        raise web.HTTPRequestEntityTooLarge(
                            max_size=MAX_UPLOAD, actual_size=count
                        )

                    # os.write may be partial even for a regular file.
                    def write_all(data=chunk):
                        view = memoryview(data)
                        while view:
                            written = os.write(descriptor, view)
                            if not written:
                                raise OSError("upload write made no progress")
                            view = view[written:]

                    await _file_io(write_all)
                await _file_io(os.fsync, descriptor)
                os.close(descriptor)
                descriptor = None
                if directory is None:
                    # Windows rename fails if the destination already exists.
                    os.rename(target, destination)
                else:
                    os.link(
                        target,
                        destination,
                        src_dir_fd=directory,
                        dst_dir_fd=directory,
                        follow_symlinks=False,
                    )
                    os.unlink(target, dir_fd=directory)
                    await _file_io(os.fsync, directory)
                completed = True
                return web.json_response(
                    {"path": str(context.root / filename), "name": name, "size": count}
                )
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                if not completed:
                    try:
                        os.unlink(target, dir_fd=directory)
                    except FileNotFoundError:
                        pass
                if directory is not None:
                    os.close(directory)

    async def download(self, request):
        self.auth.require(request)
        try:
            context = await asyncio.to_thread(
                self.application.workspaces.resolve, request.query.get("threadId", "")
            )
            path = self.application.workspaces.path(
                context, request.query.get("path", "")
            )
        except ApplicationError as exc:
            raise web.HTTPForbidden(text=exc.user_message) from exc
        # Open once; the response uses this descriptor's bytes, not a later path
        # lookup which could follow a swapped symlink.
        async with self._uploads:
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode):
                raise web.HTTPBadRequest(text="Download requires a regular file")
            descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_BINARY", 0),
            )
            try:
                info = os.fstat(descriptor)
                if not os.path.samestat(before, info):
                    raise web.HTTPConflict(text="File changed while opening download")
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_DOWNLOAD:
                    raise web.HTTPBadRequest(
                        text="Download requires a regular file up to 32 MiB"
                    )
                response = web.StreamResponse(
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(info.st_size),
                        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(path.name, safe='')}",
                    }
                )
                await response.prepare(request)
                remaining = info.st_size
                while remaining:
                    data = await _file_io(
                        os.read, descriptor, min(64 * 1024, remaining)
                    )
                    if not data:
                        raise ConnectionError("File changed during download")
                    await response.write(data)
                    remaining -= len(data)
                await response.write_eof()
                return response
            finally:
                os.close(descriptor)
