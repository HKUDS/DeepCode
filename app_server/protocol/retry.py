"""Explicit network retry contract; unlisted operations must not be replayed."""

READ_METHODS = frozenset(
    {
        "project/list",
        "project/read",
        "settings/read",
        "provider/list",
        "provider/login/poll",
        "model/list",
        "preset/list",
        "preset/current",
        "skills/list",
        "skill/read",
        "plugins/list",
        "hooks/list",
        "mcp/list",
        "mcp/presets",
        "diagnostics/read",
        "automation/list",
        "automation/runs",
        "thread/list",
        "thread/read",
        "thread/execution/read",
        "turn/list",
        "model/reasoning",
        "thread/goal/get",
        "turn/read",
        "turn/input/read",
        "terminal/list",
        "terminal/read",
        "workflow/read",
        "workflow/list",
        "artifact/list",
        "artifact/read",
        "event/replay",
        "file/list",
        "file/read",
        "git/status",
        "git/diff",
        "test/discover",
    }
)

KEYED_METHODS = {
    "turn/start": "messageId",
    "turn/enqueue": "messageId",
    "turn/steer": "messageId",
    "automation/run": "requestId",
}


def retry_capabilities() -> dict:
    return {
        "default": "never",
        "readMethods": sorted(READ_METHODS),
        "keyedMethods": dict(KEYED_METHODS),
    }
