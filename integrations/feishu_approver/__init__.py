"""Feishu approval bridge: a phone-side approver for DeepCode runs.

This package is a *separate process* that connects to a running DeepCode
app_server over its native RPC, relays ``approval.requested`` prompts to Feishu
as interactive cards, and writes decisions back via ``approval/respond``.  It
makes no changes to the core; it is an edge extension.

Run it with::

    python -m integrations.feishu_approver
"""

from __future__ import annotations

__all__ = ["ApprovalBridge", "BridgeSettings", "load_settings"]


def __getattr__(name: str):  # lazy re-export; lark_oapi import stays off the hot path
    if name == "ApprovalBridge":
        from .bridge import ApprovalBridge

        return ApprovalBridge
    if name == "BridgeSettings":
        from .config import BridgeSettings

        return BridgeSettings
    if name == "load_settings":
        from .config import load_settings

        return load_settings
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
