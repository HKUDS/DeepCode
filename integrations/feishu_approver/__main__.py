"""Entry point: ``python -m integrations.feishu_approver``.

Reads configuration from the environment (see ``config.py`` for the variables),
connects to the local DeepCode app_server, and starts relaying approval prompts
to Feishu.  Runs until interrupted; Ctrl-C stops it cleanly.

Example (POSIX)::

    export FEISHU_APP_ID=cli_xxx
    export FEISHU_APP_SECRET=xxx
    export FEISHU_OPEN_IDS=ou_xxx
    python -m integrations.feishu_approver
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from integrations.feishu_approver.config import load_settings

try:  # Optional convenience: plain env vars still work without python-dotenv.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _install_signal_handlers(bridge, loop: asyncio.AbstractEventLoop) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, bridge.stop)
        except (NotImplementedError, RuntimeError):
            # Windows / non-main thread: fall back to the default KeyboardInterrupt.
            pass


def _install_file_logging() -> None:
    """Mirror logs to ``FEISHU_APPROVER_LOG`` when set.

    ``pythonw.exe`` (Windows scheduled task) discards stderr, so without this a
    crashed bridge leaves no traceback anywhere.  A logging failure here must
    never take the bridge down, so it is swallowed (stderr still works under a
    real console).
    """
    import os

    path = os.environ.get("FEISHU_APPROVER_LOG", "").strip()
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logging.getLogger().addHandler(handler)
    except OSError:
        logging.getLogger("feishu-approver").exception(
            "failed to open FEISHU_APPROVER_LOG=%s", path
        )


async def _amain() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _install_file_logging()
    log = logging.getLogger("feishu-approver")

    try:
        settings = load_settings()
    except ValueError as exc:
        log.error("Configuration error: %s", exc)
        return 2

    # Imported here so a config error does not pay the lark_oapi import cost.
    from integrations.feishu_approver.bridge import ApprovalBridge

    bridge = ApprovalBridge(settings)
    loop = asyncio.get_running_loop()
    _install_signal_handlers(bridge, loop)

    log.info(
        "Starting Feishu approval bridge (timeout=%ss action=%s, open_ids=%d, chat_ids=%d)",
        settings.timeout_seconds,
        settings.timeout_action,
        len(settings.open_ids),
        len(settings.chat_ids),
    )
    try:
        await bridge.run()
    except Exception:
        log.exception("Feishu approval bridge crashed")
        return 1
    log.info("Feishu approval bridge stopped")
    return 0


def main() -> int:
    try:
        return asyncio.run(_amain())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
