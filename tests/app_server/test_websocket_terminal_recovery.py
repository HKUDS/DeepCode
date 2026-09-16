from __future__ import annotations

import asyncio
import os
import shlex
import sys

import pytest

from core.domain import TrustState
from tests.app_server.support import auth, body, control_server
from tests.app_server.test_websocket import initialize, rpc


@pytest.mark.skipif(os.name == "nt", reason="POSIX PTY adapter only")
@pytest.mark.parametrize("overflow", [False, True])
def test_real_pty_disconnected_exit_and_readonly_recovery(tmp_path, overflow):
    async def scenario():
        async with control_server(tmp_path) as (control, client):
            app = control.host.application
            if overflow:
                app.terminals.output_capacity = 1024
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            project = app.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
            thread = app.threads.start(project.id, title="Disconnected PTY")
            rows = 2000 if overflow else 20
            (workspace / "output.py").write_text(
                "import time, sys, termios\n"
                "attrs = termios.tcgetattr(sys.stdout.fileno())\n"
                "attrs[1] &= ~termios.OPOST\n"
                "termios.tcsetattr(sys.stdout.fileno(), termios.TCSANOW, attrs)\n"
                "open('executions.txt', 'a').write('once\\n')\n"
                "time.sleep(.3)\n"
                f"text = ''.join(f'行🙂-{{index:04d}}\\n' for index in range({rows}))\n"
                "print(text, end='', flush=True)\n"
                "open('expected.txt', 'w').write(text)\n"
            )
            ws = await client.ws_connect("/api/rpc", headers=auth(control))
            capabilities = (await initialize(ws))["capabilities"]["requestRetry"]
            assert "terminal/read" in capabilities["readMethods"]
            assert "terminal/write" not in capabilities["keyedMethods"]
            terminal = (await rpc(ws, "terminal/create", {"threadId": thread.id}))[
                "terminal"
            ]
            params = {"threadId": thread.id, "terminalId": terminal["terminalId"]}
            await rpc(
                ws,
                "terminal/write",
                {**params, "data": f"exec {shlex.quote(sys.executable)} output.py\r"},
            )
            await ws.close()
            async with asyncio.timeout(5):
                while app.terminals.active_count:
                    await asyncio.sleep(0.01)
            restored = await client.ws_connect("/api/rpc", headers=auth(control))
            await initialize(restored)
            entries = (await rpc(restored, "terminal/list", {"threadId": thread.id}))[
                "terminals"
            ]
            assert len(entries) == 1 and entries[0]["exited"]
            response = await client.post(
                "/control/rpc",
                headers=auth(control),
                json=body("drain", {"timeout": 1}),
            )
            assert (await response.json())["result"]["accepted"]
            first = await rpc(restored, "terminal/read", {**params, "limit": 64})
            page = first
            output = page["data"]
            while page["hasMore"]:
                previous = page["nextOffset"]
                page = await rpc(
                    restored,
                    "terminal/read",
                    {
                        **params,
                        "offset": previous,
                        "through": first["headOffset"],
                        "limit": 64,
                    },
                )
                assert page["offset"] == previous
                output += page["data"]
            assert page["exitCode"] == 0 and page["exited"]
            assert first["truncated"] is overflow
            expected = (workspace / "expected.txt").read_text()
            if overflow:
                assert len(output.encode()) <= 1024
                assert expected.encode().endswith(output.encode())
            else:
                assert output.endswith(expected)
                assert output.count("行🙂-0000") == 1
            assert (workspace / "executions.txt").read_text() == "once\n"
            assert (
                await rpc(
                    restored, "terminal/read", {**params, "offset": page["nextOffset"]}
                )
            )["data"] == ""

    asyncio.run(asyncio.wait_for(scenario(), 15))
