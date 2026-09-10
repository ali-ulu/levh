"""Regression: broadcast_agent_event must not raise UnboundLocalError.

The function used `_agent_ws_clients -= stale`, an augmented assignment that
makes the module-level set function-local, so the early `if not
_agent_ws_clients` read raised UnboundLocalError on every call — 500-ing
every agent REST endpoint that broadcasts (connect/heartbeat/disconnect).
"""

import pytest

from server.routes.agents import broadcast_agent_event, _agent_ws_clients


@pytest.mark.asyncio
async def test_broadcast_agent_event_runs_with_clients():
    ws_clients = _agent_ws_clients
    ws_clients.add(object())  # type: ignore[arg-type]
    try:
        # Before the fix this raised UnboundLocalError.
        await broadcast_agent_event("agent_connected", {"agent_name": "x"})
        assert not ws_clients  # the fake client was stale and pruned
    finally:
        ws_clients.clear()


@pytest.mark.asyncio
async def test_broadcast_agent_event_runs_without_clients():
    # Empty set: the early-return path must also work.
    await broadcast_agent_event("agent_connected", {"agent_name": "x"})