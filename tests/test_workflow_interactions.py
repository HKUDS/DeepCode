from __future__ import annotations

import pytest

from workflows.interactions import (
    InteractionHandler,
    InteractionPoint,
    InteractionRegistry,
)
from workflows.interactions.base import InteractionRequest, InteractionResponse


class _Handler(InteractionHandler):
    name = "test-handler"
    hook_point = InteractionPoint.BEFORE_PLANNING

    async def should_trigger(self, context):
        return True

    async def create_interaction(self, context):
        return InteractionRequest(
            interaction_type="test",
            title="Test",
            description="Test handler",
            data={},
        )

    async def process_response(self, response: InteractionResponse, context):
        context["processed"] = response.action
        return context

    async def on_skip(self, context):
        context["skipped"] = True
        return context


@pytest.mark.asyncio
async def test_interaction_registry_lifecycle_has_no_plugin_semantics() -> None:
    registry = InteractionRegistry()
    handler = _Handler()
    registry.register(handler)

    assert registry.get_handlers(InteractionPoint.BEFORE_PLANNING) == [handler]
    assert await registry.run_hook(InteractionPoint.BEFORE_PLANNING, {}) == {
        "skipped": True
    }

    assert registry.disable(handler.name) is True
    assert await registry.run_hook(InteractionPoint.BEFORE_PLANNING, {}) == {}
    assert registry.enable(handler.name) is True
    assert registry.unregister(handler.name) is True
    assert registry.get_handlers(InteractionPoint.BEFORE_PLANNING) == []
