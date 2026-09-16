"""No-key service acceptance runner; duration is measured, never accelerated.

Run against an isolated source checkout and private --root. The worker uses a
controlled Agent fixture; HTTP/WS, persistence, approvals and PTYs are real.
This is not a live-model or operating-system login test.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))


def worker(root):
    from loguru import logger
    from app_server.service import ControlServer, serve
    from app_server.service_state import ServiceFiles
    from core.application import DeepCodeApplication
    from core.events import AgentMessage, Event, TaskComplete, TurnStarted

    logger.remove()
    logger.add(root / "worker.log", rotation="5 MB", retention=2)

    class Factory:
        def create(self, *, workspace, model, approval_callback):
            class Session:
                def load_history(self, _history):
                    pass

                async def run_stream(self, operation):
                    yield Event("started", TurnStarted())
                    approved = await approval_callback(
                        "write",
                        {"file_path": "effects.jsonl", "content": operation.text},
                        "soak verification write",
                    )
                    if approved:
                        await asyncio.sleep(0.05)
                        with Path(workspace, "effects.jsonl").open("a") as output:
                            output.write(json.dumps(operation.text) + "\n")
                    yield Event("message", AgentMessage("verified"))
                    yield Event("done", TaskComplete("verified", "completed"))

                async def aclose(self):
                    pass

            return Session()

    original_status = ControlServer.status

    async def instrumented_status(control):
        value = await original_status(control)
        value["soak"] = {
            "connections": len(control.business._connections),
            "peers": len(control.host._peers),
            "pendingRpc": len(control.business._inflight),
        }
        return value

    ControlServer.status = instrumented_status
    original = DeepCodeApplication.open
    DeepCodeApplication.open = staticmethod(
        lambda *args, **kwargs: original(*args, session_factory=Factory(), **kwargs)
    )
    asyncio.run(serve(ServiceFiles(root / "state.sqlite3"), 0))


def percentile(values, fraction):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * fraction))] if values else None


def resources(pid):
    if os.name == "nt":
        command = f"$p=Get-Process -Id {int(pid)}; @{{rssKiB=[math]::Round($p.WorkingSet64/1024); handleCount=$p.HandleCount; threadCount=$p.Threads.Count}} | ConvertTo-Json -Compress"
        return json.loads(
            subprocess.check_output(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    command,
                ],
                text=True,
                timeout=5,
            )
        )
    # RSS is comparable on macOS and Linux; no optional metrics dependency.
    value = subprocess.check_output(
        ["ps", "-o", "rss=", "-p", str(pid)], text=True, timeout=5
    )
    result = {"rssKiB": int(value.strip())}
    proc = Path(f"/proc/{pid}/fd")
    if proc.is_dir():
        result["fdCount"] = len(list(proc.iterdir()))
        result["threadCount"] = len(list(Path(f"/proc/{pid}/task").iterdir()))
    elif sys.platform == "darwin":
        descriptors = subprocess.check_output(
            ["/usr/sbin/lsof", "-p", str(pid), "-F", "f"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        result["fdCount"] = sum(
            line.startswith("f") for line in descriptors.splitlines()
        )
        threads = subprocess.check_output(
            ["ps", "-M", "-p", str(pid)], text=True, timeout=5
        )
        result["threadCount"] = max(0, len(threads.splitlines()) - 1)
    return result


async def run(root, seconds, interval):
    import aiohttp
    from app_server.errors import RpcError
    from app_server.native_client import NativeRpcClient
    from app_server.service_client import ServiceClient, ServiceUnavailable
    from app_server.service_state import ServiceFiles
    from core.private_storage import atomic_write_private_json

    if (root / "state.sqlite3").exists() or (root / "status.json").exists():
        raise ValueError("Use a new isolated root for each soak run")
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    home = root / "home"
    home.mkdir(mode=0o700)
    workspace = root / "workspace"
    workspace.mkdir()
    atomic_write_private_json(
        home / "deepcode_config.json",
        {
            "agents": {"defaults": {"connection": "fixture", "model": "test-model"}},
            "providers": {
                "profiles": {
                    "fixture": {
                        "template": "custom",
                        "protocol": "openai_chat",
                        "auth": "none",
                        "apiBase": "http://127.0.0.1:9/v1",
                    }
                }
            },
        },
    )
    env = {
        **os.environ,
        "DEEPCODE_HOME": str(home),
        "DEEPCODE_SESSIONS_DIR": str(home / "sessions"),
        "PYTHONPATH": str(REPOSITORY),
    }
    env = {
        key: value
        for key, value in env.items()
        if not re.search(r"_API_KEY$|^LANGFUSE_|^ANTHROPIC_AUTH_TOKEN$", key)
    }
    files = ServiceFiles(root / "state.sqlite3")
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--root",
            str(root),
        ],
        cwd=REPOSITORY,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    started = time.monotonic()
    samples = []
    attach_times = []
    recovery_times = []
    cycles = 0
    terminals = 0
    browser_checks = 0
    clients = []
    outcome = {
        "status": "running",
        "requestedSeconds": seconds,
        "startedAt": time.time(),
        "workerPid": process.pid,
        "root": str(root),
    }
    atomic_write_private_json(root / "status.json", outcome)
    initialize = {
        "protocolVersion": "1.0",
        "clientInfo": {"name": "foundation-soak", "version": "1", "surface": "desktop"},
    }

    async def connect():
        client = NativeRpcClient(files)
        at = time.monotonic()
        await client.connect(initialize)
        attach_times.append(time.monotonic() - at)
        return client

    async def wait_turn(client, identity, *, terminal=False):
        async with asyncio.timeout(15):
            while True:
                snapshot = await client.request("turn/read", {"turnId": identity})
                if terminal and snapshot["turn"]["status"] in {
                    "completed",
                    "failed",
                    "interrupted",
                }:
                    return snapshot
                if not terminal and snapshot["approvals"]:
                    return snapshot
                await asyncio.sleep(0.03)

    try:
        async with asyncio.timeout(15):
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Worker exited during startup")
                try:
                    await asyncio.to_thread(ServiceClient(files).call, "status")
                    break
                except ServiceUnavailable:
                    await asyncio.sleep(0.05)
        cold_seconds = time.monotonic() - started
        started = time.monotonic()
        outcome["startedAt"] = time.time()
        first = await connect()
        clients = [first]
        project = (await first.request("project/add", {"path": str(workspace)}))[
            "project"
        ]
        await first.request(
            "project/update", {"projectId": project["id"], "trustState": "trusted"}
        )
        last_wall = time.time()
        max_wall_gap = 0.0
        while time.monotonic() - started < seconds:
            wall = time.time()
            max_wall_gap = max(max_wall_gap, wall - last_wall)
            if seconds >= 3600 and max_wall_gap > max(60, interval * 3):
                raise AssertionError(
                    "Continuous soak interrupted by a prolonged scheduling/sleep gap"
                )
            last_wall = wall
            cycle_started = time.monotonic()
            if cycles % 10 == 0:
                thread = (
                    await first.request(
                        "thread/start",
                        {
                            "projectId": project["id"],
                            "title": f"Soak cohort {cycles}",
                            "contextWindow": 32000,
                        },
                    )
                )["thread"]
                cursor = 0
            params = {
                "threadId": thread["id"],
                "prompt": f"effect-{cycles}",
                "messageId": f"soak-{cycles}",
            }
            admission = await first.request("turn/start", params)
            identity = admission["turn"]["id"]
            # Reconnect ten observers while one admitted task is waiting.
            await asyncio.gather(*(client.close() for client in clients))
            clients = list(await asyncio.gather(*(connect() for _ in range(10))))
            first = clients[0]
            duplicate = await clients[1].request("turn/start", params)
            assert duplicate["turn"]["id"] == identity
            snapshot = await wait_turn(first, identity)
            approval = snapshot["approvals"][0]["id"]
            await first.request(
                "approval/respond",
                {"approvalId": approval, "decision": "approved_once"},
            )
            try:
                await clients[1].request(
                    "approval/respond", {"approvalId": approval, "decision": "denied"}
                )
                raise AssertionError("Conflicting second approval succeeded")
            except RpcError:
                pass
            snapshot = await wait_turn(first, identity, terminal=True)
            assert snapshot["turn"]["status"] == "completed"
            assert snapshot["turn"]["executionProfile"]["contextWindow"] == 32000
            effects = (workspace / "effects.jsonl").read_text().splitlines()
            assert effects.count(json.dumps(f"effect-{cycles}")) == 1
            at = time.monotonic()
            through = None
            while True:
                page = await first.request(
                    "event/replay",
                    {
                        "threadId": thread["id"],
                        "after": cursor,
                        "limit": 7,
                        **({"through": through} if through is not None else {}),
                    },
                )
                if through is None:
                    through = page["headSequence"]
                for event in page["events"]:
                    assert event["sequence"] == cursor + 1
                    cursor = event["sequence"]
                if not page["hasMore"]:
                    break
            assert cursor == through
            recovery_times.append(time.monotonic() - at)
            if cycles % 10 == 0 and os.name != "nt":
                terminal = (
                    await first.request("terminal/create", {"threadId": thread["id"]})
                )["terminal"]
                target = {
                    "threadId": thread["id"],
                    "terminalId": terminal["terminalId"],
                }
                await first.request(
                    "terminal/write",
                    {**target, "data": "printf 'soak-terminal-ok\\n'; exit\r"},
                )
                async with asyncio.timeout(10):
                    while True:
                        output = await clients[1].request("terminal/read", target)
                        if output["exited"]:
                            break
                        await asyncio.sleep(0.05)
                assert "soak-terminal-ok" in output["data"]
                terminals += 1
            if cycles % 10 == 0:
                record = files.read()[0]
                ticket = await asyncio.to_thread(
                    ServiceClient(files).call, "auth/issue"
                )
                async with aiohttp.ClientSession(
                    cookie_jar=aiohttp.CookieJar(unsafe=True)
                ) as browser:
                    async with browser.post(
                        record.url + "/auth/exchange",
                        json={"ticket": ticket["ticket"]},
                        headers={"Origin": record.url},
                    ) as response:
                        assert response.status == 200
                    async with browser.get(record.url + "/api/session") as response:
                        assert response.status == 200
                browser_checks += 1
            status = await asyncio.to_thread(ServiceClient(files).call, "status")
            assert (
                status["activeTurns"]
                == status["queuedTurns"]
                == status["terminals"]
                == 0
            )
            assert status["soak"] == {
                "connections": 10,
                "peers": 10,
                "pendingRpc": 0,
            }, status["soak"]
            sample = {
                "elapsedSeconds": time.monotonic() - started,
                "wallTime": time.time(),
                **resources(process.pid),
            }
            samples.append(sample)
            cycles += 1
            outcome.update(
                elapsedSeconds=sample["elapsedSeconds"],
                maxWallGapSeconds=max_wall_gap,
                cycles=cycles,
                connections=len(attach_times),
                terminals=terminals,
                browserChecks=browser_checks,
                attachP95=percentile(attach_times, 0.95),
                recoveryP95=percentile(recovery_times, 0.95),
                coldStartSeconds=cold_seconds,
                **resources(process.pid),
            )
            atomic_write_private_json(root / "status.json", outcome)
            with (root / "samples.jsonl").open("a") as stream:
                stream.write(json.dumps(sample) + "\n")
            await asyncio.sleep(
                max(
                    0,
                    min(
                        interval - (time.monotonic() - cycle_started),
                        seconds - (time.monotonic() - started),
                    ),
                )
            )
        max_wall_gap = max(max_wall_gap, time.time() - last_wall)
        assert seconds < 3600 or max_wall_gap <= max(60, interval * 3), (
            "Continuous soak interrupted by sleep"
        )
        assert cycles > 0
        assert percentile(attach_times, 0.95) <= 2
        assert percentile(recovery_times, 0.95) <= 3
        assert cold_seconds <= 10
        baseline = samples[min(10, len(samples) - 1)]["rssKiB"]
        recent = [sample["rssKiB"] for sample in samples[-10:]]
        for resource in ("fdCount", "handleCount", "threadCount"):
            if resource in samples[-1]:
                assert (
                    samples[-1][resource]
                    <= samples[min(10, len(samples) - 1)][resource] + 16
                ), f"Persistent {resource} growth"
        outcome.update(
            status="passed",
            elapsedSeconds=time.monotonic() - started,
            rssBaselineKiB=baseline,
            rssTailMedianKiB=percentile(recent, 0.5),
        )
        if seconds >= 3600 and len(samples) >= 40:
            quarters = [
                samples[len(samples) * part // 4 : len(samples) * (part + 1) // 4]
                for part in range(4)
            ]
            medians = [
                percentile([sample["rssKiB"] for sample in quarter], 0.5)
                for quarter in quarters
            ]
            outcome["rssQuarterMediansKiB"] = medians
            if (
                all(right > left for left, right in zip(medians, medians[1:]))
                and medians[-1] - medians[0] > 16 * 1024
            ):
                raise AssertionError("Sustained RSS growth across all four quarters")
        if len(samples) > 20 and percentile(recent, 0.5) > baseline + 64 * 1024:
            raise AssertionError(
                "Worker RSS grew more than the 64 MiB investigation threshold"
            )
    except BaseException as exc:
        outcome.update(
            status="failed",
            elapsedSeconds=time.monotonic() - started,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    finally:
        await asyncio.gather(
            *(client.close() for client in clients), return_exceptions=True
        )
        if process.poll() is None:
            try:
                async with asyncio.timeout(5):
                    while True:
                        drained = await asyncio.to_thread(
                            ServiceClient(files).call, "status"
                        )
                        if drained["soak"] == {
                            "connections": 0,
                            "peers": 0,
                            "pendingRpc": 0,
                        }:
                            break
                        await asyncio.sleep(0.02)
                await asyncio.to_thread(
                    ServiceClient(files).call,
                    "stop",
                    {"timeout": 10, "cancelRunning": True},
                    timeout=20,
                )
                await asyncio.to_thread(process.wait, 15)
            except Exception:
                process.kill()
                process.wait(timeout=10)
                outcome["cleanupForced"] = True
                outcome["status"] = "failed"
        outcome["workerExitCode"] = process.returncode
        if process.returncode != 0:
            outcome["status"] = "failed"
            outcome.setdefault("error", "The service worker did not shut down cleanly")
        atomic_write_private_json(root / "status.json", outcome)
        print(json.dumps(outcome), flush=True)
        if outcome["status"] == "failed" and sys.exception() is None:
            raise RuntimeError("Soak cleanup failed; inspect status.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=86400)
    parser.add_argument("--interval", type=float, default=20)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if args.seconds < 1 or args.interval < 0:
        parser.error("duration must be positive and interval non-negative")
    if args.worker:
        worker(args.root.absolute())
    else:
        asyncio.run(run(args.root.absolute(), args.seconds, args.interval))
