"""Interactive Feishu card construction for approval prompts.

The card is deliberately minimal: a header colour encodes urgency, the body
summarises the pending tool call, and three action buttons carry the decision.
Only ``approval_id`` rides along in the button ``value`` — never secrets or
full arguments — so a leaked card cannot leak more than the opaque id.

Card JSON follows the Feishu "card 1.0" schema (``config.wide_screen_mode``,
``header.template``, ``elements`` with ``tag: "action"`` + ``button``).  The
bridge never imports the SDK here so the card can be unit-tested offline.
"""

from __future__ import annotations

from typing import Any

# ``approval_view`` category -> Feishu header colour.  Unknown categories fall
# back to the neutral "blue".  Colours are cosmetic only; authorization is
# decided by the open_id allowlist, never by the card colour.
_CATEGORY_COLORS: dict[str, str] = {
    # Canonical ApprovalCategory values written by core's approval service.
    "command": "orange",
    "file_write": "blue",
    "network": "red",
    "external_tool": "orange",
    "destructive": "red",
    # Legacy/alternate spellings kept so older payloads still colour sensibly.
    "file": "blue",
    "secret": "red",
    "permission": "orange",
}

# Button ``value.action`` -> decision string accepted by the ``approval/respond``
# RPC.  Kept explicit (not derived) so an accidental rename is caught by tests.
DECISIONS: dict[str, str] = {
    "approve_once": "approved_once",
    "approve_session": "approved_session",
    "deny": "denied",
}


def _truncate(text: str, limit: int = 600) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _button(value: dict[str, str], text: str, style: str | None) -> dict[str, Any]:
    button: dict[str, Any] = {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": "primary" if style is None else "default",
        "value": value,
    }
    if style is not None:
        button["type"] = style
    return button


def _timeout_note(timeout_seconds: int, timeout_action: str) -> str:
    """Human-readable auto-resolution note shown in the card footer."""
    if timeout_seconds <= 0:
        return "No auto-resolution."
    if timeout_action == "approve_session":
        return f"Auto-approved for this session in {timeout_seconds}s if unanswered."
    if timeout_action == "approve_once":
        return f"Auto-approved once in {timeout_seconds}s if unanswered."
    return f"Auto-denied in {timeout_seconds}s."


def build_prompt_card(
    approval: dict[str, Any],
    *,
    timeout_seconds: int,
    timeout_action: str = "deny",
) -> dict[str, Any]:
    """Build the interactive approval card.

    ``approval`` is the ``approval_view`` payload from the ``approval.requested``
    event (``{id, category, status, request, ...}``).  ``request`` is expected to
    be a dict with ``toolName`` (core's canonical camelCase; ``tool_name`` is
    accepted as a legacy fallback) / ``arguments`` / ``reason``.
    """
    approval_id = str(approval.get("id", ""))
    request = approval.get("request") or {}
    category = str(approval.get("category", "unknown"))
    tool_name = str(
        request.get("toolName") or request.get("tool_name") or "unknown tool"
    )
    arguments = request.get("arguments") or {}
    reason = str(request.get("reason") or "").strip()

    # Pretty-print arguments compactly; a long JSON blob is truncated so the
    # card stays readable on a phone screen.
    import json

    try:
        args_text = json.dumps(arguments, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        args_text = str(arguments)
    args_text = _truncate(args_text, 500)

    timeout_note = _timeout_note(timeout_seconds, timeout_action)
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**Tool:** `{_truncate(tool_name, 120)}`\n**Category:** `{category}`",
            },
        },
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**Arguments:**\n`{_truncate(args_text, 500)}`",
            },
        },
    ]
    if reason:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**Reason:** {_truncate(reason, 400)}",
                },
            }
        )
    elements.append({"tag": "hr"})
    elements.append(
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": f"{timeout_note} Only allowlisted open_ids count.",
                },
            ],
        }
    )
    elements.append(
        {
            "tag": "action",
            "actions": [
                _button(
                    {"approval_id": approval_id, "action": "approve_once"},
                    "Approve once",
                    "primary",
                ),
                _button(
                    {"approval_id": approval_id, "action": "approve_session"},
                    "Approve for session",
                    None,
                ),
                _button(
                    {"approval_id": approval_id, "action": "deny"}, "Deny", "danger"
                ),
            ],
        }
    )

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _CATEGORY_COLORS.get(category, "blue"),
            "title": {"tag": "plain_text", "content": "DeepCode approval required"},
        },
        "elements": elements,
    }


def build_resolved_card(approval_id: str, *, decision: str, by: str) -> dict[str, Any]:
    """Build the follow-up card shown after a decision is recorded."""
    label = {
        "approved_once": "Approved (once)",
        "approved_session": "Approved (session)",
        "denied": "Denied",
    }.get(decision, decision)
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green" if decision.startswith("approved") else "red",
            "title": {"tag": "plain_text", "content": f"DeepCode approval: {label}"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"Decision: **{label}**\nBy: `{by}`",
                },
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": f"Approval `{approval_id}` · decision `{decision}`",
                    }
                ],
            },
        ],
    }
