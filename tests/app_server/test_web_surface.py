from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from core.domain import TrustState
from core.version import __version__
from tests.app_server.support import auth, body, control_server


async def browser_headers(control, client):
    ticket = (
        await (
            await client.post(
                "/control/rpc", headers=auth(control), json=body("auth/issue")
            )
        ).json()
    )["result"]["ticket"]
    response = await client.post(
        "/auth/exchange",
        headers={"Origin": control.record.url},
        json={"ticket": ticket},
    )
    cookie = next(iter(response.cookies.values()))
    return {"Origin": control.record.url, "Cookie": f"{cookie.key}={cookie.value}"}


def test_static_assets_missing_build_and_path_boundaries(tmp_path):
    async def scenario():
        async with control_server(tmp_path) as (control, client):
            assets = tmp_path / "assets-root"
            assets.mkdir()
            control.web_surface.assets = assets
            response = await client.get("/")
            assert response.status == 503
            assert "build:web" in await response.text()
            (assets / "assets").mkdir()
            (assets / "index.html").write_text('<script src="/assets/app.js"></script>')
            (assets / "assets" / "app.js").write_text("console.log('app')")
            (assets / "web-build.json").write_text(
                json.dumps({"version": __version__, "buildId": "test-build"})
            )
            response = await client.get("/")
            assert response.status == 200
            assert (
                "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
            )
            assert response.headers["Cache-Control"] == "no-store"
            assert (await client.get("/assets/app.js")).status == 200
            outside = tmp_path / "secret.txt"
            outside.write_text("private")
            (assets / "assets" / "escape.js").symlink_to(outside)
            assert (await client.get("/assets/escape.js")).status == 404
            assert (await client.get("/api/session")).status == 401
            headers = await browser_headers(control, client)
            assert (await client.get("/api/session", headers=headers)).status == 200
            assert (
                await client.get(
                    "/api/session",
                    headers={**headers, "Origin": "https://evil.invalid"},
                )
            ).status == 403

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor containment test")
def test_upload_download_trust_size_origin_and_symlinks(tmp_path):
    async def scenario():
        async with control_server(tmp_path) as (control, client):
            app = control.host.application
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            project = app.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
            thread = app.threads.start(project.id, title="Browser files")
            headers = {
                **await browser_headers(control, client),
                "Content-Type": "application/octet-stream",
            }
            payload = "Browser attachment 汉🙂\n".encode() * 2000
            url = f"/api/uploads?threadId={thread.id}&name=../context.txt"
            assert (await client.post(url, data=b"denied")).status == 403
            assert (
                await client.post(
                    url,
                    headers={**headers, "Origin": "https://evil.invalid"},
                    data=b"denied",
                )
            ).status == 403
            response = await client.post(url, headers=headers, data=payload)
            assert response.status == 200
            uploaded = await response.json()
            path = Path(uploaded["path"])
            assert path.parent == workspace and path.name.startswith("deepcode-upload-")
            assert path.read_bytes() == payload
            response = await client.get(
                f"/api/download?threadId={thread.id}&path={path.name}", headers=headers
            )
            assert response.status == 200 and await response.read() == payload
            assert response.headers["Content-Type"] == "application/octet-stream"
            assert response.headers["Content-Disposition"].startswith("attachment;")
            before = set(workspace.iterdir())
            app.projects.update(project.id, trust_state=TrustState.UNTRUSTED)
            assert (
                await client.post(url, headers=headers, data=b"not trusted")
            ).status == 403
            app.projects.update(project.id, trust_state=TrustState.TRUSTED)
            response = await client.post(
                url, headers=headers, data=b"x" * (10 * 1024 * 1024 + 1)
            )
            assert response.status == 413 and set(workspace.iterdir()) == before
            outside = tmp_path / "outside.txt"
            outside.write_text("private")
            (workspace / "escape.txt").symlink_to(outside)
            assert (
                await client.get(
                    f"/api/download?threadId={thread.id}&path=escape.txt",
                    headers=headers,
                )
            ).status == 403
            os.mkfifo(workspace / "fifo")
            assert (
                await client.get(
                    f"/api/download?threadId={thread.id}&path=fifo", headers=headers
                )
            ).status == 400
            control.phase = "drained"
            assert (
                await client.post(url, headers=headers, data=b"paused")
            ).status == 503
            assert (
                await client.get(
                    f"/api/download?threadId={thread.id}&path={path.name}",
                    headers=headers,
                )
            ).status == 200

    asyncio.run(asyncio.wait_for(scenario(), 20))


@pytest.mark.parametrize("cancel", [False, True])
def test_upload_is_published_only_when_complete_and_cleans_up_on_cancel(
    tmp_path, cancel
):
    async def scenario():
        async with control_server(tmp_path) as (control, client):
            app = control.host.application
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            project = app.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
            thread = app.threads.start(project.id, title="Atomic upload")
            headers = {
                **await browser_headers(control, client),
                "Content-Type": "application/octet-stream",
            }
            release = asyncio.Event()

            async def chunks():
                yield b"first"
                await release.wait()
                yield b"second"

            pending = asyncio.ensure_future(
                client.post(
                    f"/api/uploads?threadId={thread.id}&name=sample.txt",
                    headers=headers,
                    data=chunks(),
                )
            )
            async with asyncio.timeout(5):
                while not list(workspace.glob(".deepcode-upload-*.part")):
                    await asyncio.sleep(0.01)
            assert list(workspace.glob("deepcode-upload-*")) == []
            if cancel:
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                release.set()
                async with asyncio.timeout(5):
                    while list(workspace.glob(".deepcode-upload-*.part")):
                        await asyncio.sleep(0.01)
                assert list(workspace.iterdir()) == []
            else:
                release.set()
                response = await pending
                assert response.status == 200
                path = Path((await response.json())["path"])
                assert path.read_bytes() == b"firstsecond"
                assert list(workspace.glob(".deepcode-upload-*.part")) == []

    asyncio.run(asyncio.wait_for(scenario(), 15))
