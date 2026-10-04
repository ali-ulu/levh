"""Slack connector — auth, mapping, pagination, and bounds.

The real connector code runs against httpx.MockTransport, so request shape and
Slack response handling are exercised without touching the network.
"""

import httpx
import pytest

from server.connectors import get_connector, list_connectors
from server.connectors.slack import SlackConnector

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _client_with(handler) -> httpx.AsyncClient:
    return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler))


def _patch(monkeypatch, handler) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _client_with(handler))


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_CHANNEL_IDS", raising=False)


def test_slack_connector_is_registered():
    names = {c["name"] for c in list_connectors()}
    assert "slack" in names
    assert get_connector("slack").name == "slack"
    assert set(get_connector("slack").required_config_keys()) == {
        "bot_token",
        "channel_ids",
    }


@pytest.mark.asyncio
async def test_connect_requires_token_and_channels():
    with pytest.raises(ValueError, match="bot token"):
        await SlackConnector().connect({"channel_ids": ["C1"]})
    with pytest.raises(ValueError, match="channel_ids"):
        await SlackConnector().connect({"bot_token": "xoxb-test"})


@pytest.mark.asyncio
async def test_connect_uses_env_fallback_and_bearer_auth(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-env")
    monkeypatch.setenv("SLACK_CHANNEL_IDS", "C1, C2")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/auth.test"
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200, json={"ok": True, "team_id": "T1", "team": "LEVH"}
        )

    _patch(monkeypatch, handler)
    conn = SlackConnector()
    assert await conn.connect({})
    assert seen["auth"] == "Bearer xoxb-env"
    assert conn._channel_ids == ["C1", "C2"]
    assert conn._team_id == "T1"


@pytest.mark.asyncio
async def test_connect_rejects_slack_api_auth_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})

    _patch(monkeypatch, handler)
    with pytest.raises(ConnectionError, match="invalid_auth"):
        await SlackConnector().connect(
            {"bot_token": "bad", "channel_ids": ["C1"]}
        )


@pytest.mark.asyncio
async def test_connect_validates_message_bound():
    conn = SlackConnector()
    with pytest.raises(ValueError, match="integer"):
        await conn.connect(
            {"bot_token": "x", "channel_ids": ["C1"], "max_messages": "many"}
        )
    with pytest.raises(ValueError, match="at least 1"):
        await conn.connect(
            {"bot_token": "x", "channel_ids": ["C1"], "max_messages": 0}
        )


def test_message_maps_to_memory_with_provenance():
    conn = SlackConnector()
    conn._team_id = "T1"
    conn._team_name = "LEVH"
    memory = conn._to_memory(
        "C123",
        {
            "type": "message",
            "user": "U1",
            "text": "Ship the Slack connector after the roadmap fix.",
            "ts": "1760000000.125",
            "thread_ts": "1759999999.000",
            "reply_count": 3,
        },
    )
    assert memory is not None
    assert memory["content"].startswith("Ship the Slack connector")
    assert memory["tags"] == ["slack", "message", "channel:C123", "thread"]
    meta = memory["metadata"]
    assert meta["channel_id"] == "C123"
    assert meta["user_id"] == "U1"
    assert meta["team_id"] == "T1"
    assert meta["reply_count"] == 3
    assert meta["captured_at"].endswith("+00:00")


def test_empty_message_is_skipped():
    assert SlackConnector()._to_memory("C1", {"text": "   "}) is None


@pytest.mark.asyncio
async def test_history_follows_cursor_and_uses_conservative_page_size(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth.test":
            return httpx.Response(200, json={"ok": True, "team_id": "T1"})
        calls.append(
            {
                "channel": request.url.params.get("channel"),
                "cursor": request.url.params.get("cursor"),
                "limit": request.url.params.get("limit"),
                "oldest": request.url.params.get("oldest"),
            }
        )
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "messages": [{"text": "newest", "ts": "1760000002.0"}],
                    "response_metadata": {"next_cursor": "cursor-2"},
                },
            )
        return httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [{"text": "older", "ts": "1760000001.0"}],
                "response_metadata": {"next_cursor": ""},
            },
        )

    _patch(monkeypatch, handler)
    conn = SlackConnector()
    await conn.connect(
        {
            "bot_token": "xoxb-test",
            "channel_ids": ["C1"],
            "oldest": "1750000000.0",
            "max_messages": 100,
        }
    )
    memories = await conn.fetch()

    assert [m["content"] for m in memories] == ["newest", "older"]
    assert calls == [
        {"channel": "C1", "cursor": None, "limit": "15", "oldest": "1750000000.0"},
        {
            "channel": "C1",
            "cursor": "cursor-2",
            "limit": "15",
            "oldest": "1750000000.0",
        },
    ]


@pytest.mark.asyncio
async def test_fetch_walks_channels_and_stops_at_global_max(monkeypatch):
    history_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth.test":
            return httpx.Response(200, json={"ok": True})
        channel = request.url.params["channel"]
        history_calls.append(channel)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [
                    {"text": f"{channel}-1", "ts": "1760000002.0"},
                    {"text": f"{channel}-2", "ts": "1760000001.0"},
                ],
                "response_metadata": {"next_cursor": ""},
            },
        )

    _patch(monkeypatch, handler)
    conn = SlackConnector()
    await conn.connect(
        {
            "bot_token": "xoxb-test",
            "channel_ids": ["C1", "C2"],
            "max_messages": 3,
        }
    )
    memories = await conn.fetch()

    assert [m["content"] for m in memories] == ["C1-1", "C1-2", "C2-1"]
    assert history_calls == ["C1", "C2"]


@pytest.mark.asyncio
async def test_fetch_fails_loudly_on_channel_api_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth.test":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(
            200, json={"ok": False, "error": "channel_not_found"}
        )

    _patch(monkeypatch, handler)
    conn = SlackConnector()
    await conn.connect({"bot_token": "xoxb-test", "channel_ids": ["C-missing"]})
    with pytest.raises(ConnectionError, match="channel_not_found"):
        await conn.fetch()
