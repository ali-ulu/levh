"""Slack Connector — Pull channel history from Slack via the Web API.

The connector is deliberately pull-on-demand. It uses an operator-provided bot
token and explicit channel IDs, so it does not need channel-discovery scopes or
a background worker of its own. Existing connector sync/background-job
surfaces decide when it runs.

Config keys:
    bot_token (str): Slack bot token (or SLACK_BOT_TOKEN).
    channel_ids (list[str] | str): Conversation IDs to import
        (or comma-separated SLACK_CHANNEL_IDS).
    max_messages (int, optional): Global cap across all channels. Default 100.
    oldest (str, optional): Slack timestamp lower bound.

Thread expansion is intentionally not part of this first slice:
conversations.replies multiplies API calls per parent message and deserves a
separate opt-in with its own rate-limit contract.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from .base import BaseConnector

SLACK_API = "https://slack.com/api"
_PAGE_SIZE = 15


def _channel_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        items = []
    return [str(item).strip() for item in items if str(item).strip()]


class SlackConnector(BaseConnector):
    """Import message history from explicit Slack conversations."""

    name: str = "slack"
    description: str = (
        "Import Slack channel history with a bot token and explicit channel IDs. "
        "Uses cursor pagination and the existing pull-on-demand sync pipeline."
    )

    def __init__(self) -> None:
        self._headers: dict[str, str] = {}
        self._channel_ids: list[str] = []
        self._max_messages: int = 100
        self._oldest: str = ""
        self._team_id: str = ""
        self._team_name: str = ""

    def required_config_keys(self) -> list[str]:
        return ["bot_token", "channel_ids"]

    async def connect(self, config: dict) -> bool:
        """Validate credentials and normalize channel/config bounds."""
        bot_token = config.get("bot_token", "") or os.getenv("SLACK_BOT_TOKEN", "")
        channels = _channel_ids(
            config.get("channel_ids") or os.getenv("SLACK_CHANNEL_IDS", "")
        )
        if not bot_token:
            raise ValueError(
                "Slack bot token is required. Pass config['bot_token'] or set "
                "SLACK_BOT_TOKEN."
            )
        if not channels:
            raise ValueError(
                "Slack channel_ids are required. Pass a list/comma-separated value "
                "or set SLACK_CHANNEL_IDS."
            )

        try:
            max_messages = int(config.get("max_messages", 100))
        except (TypeError, ValueError) as exc:
            raise ValueError("Slack max_messages must be an integer.") from exc
        if max_messages < 1:
            raise ValueError("Slack max_messages must be at least 1.")

        self._headers = {
            "Authorization": f"Bearer {bot_token}",
            "Accept": "application/json",
        }
        self._channel_ids = channels
        self._max_messages = max_messages
        self._oldest = str(config.get("oldest", "") or "").strip()

        import httpx

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0, pool=10.0)
        ) as client:
            try:
                resp = await client.post(f"{SLACK_API}/auth.test", headers=self._headers)
            except httpx.HTTPError as exc:
                raise ConnectionError(f"Slack auth.test failed: {exc}") from exc
            if resp.status_code != 200:
                raise ConnectionError(
                    f"Slack auth.test returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            payload = resp.json()
            if not payload.get("ok"):
                raise ConnectionError(
                    f"Slack rejected the bot token: {payload.get('error', 'unknown_error')}"
                )
            self._team_id = str(payload.get("team_id", "") or "")
            self._team_name = str(payload.get("team", "") or "")
        return True

    async def fetch(self, **kwargs: Any) -> list[dict]:
        """Fetch bounded, cursor-paginated history for configured channels."""
        import httpx

        memories: list[dict] = []
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0, pool=10.0)
        ) as client:
            for channel_id in self._channel_ids:
                cursor = ""
                while len(memories) < self._max_messages:
                    params: dict[str, Any] = {
                        "channel": channel_id,
                        "limit": min(_PAGE_SIZE, self._max_messages - len(memories)),
                    }
                    if cursor:
                        params["cursor"] = cursor
                    if self._oldest:
                        params["oldest"] = self._oldest

                    try:
                        resp = await client.get(
                            f"{SLACK_API}/conversations.history",
                            headers=self._headers,
                            params=params,
                        )
                    except httpx.HTTPError as exc:
                        raise ConnectionError(
                            f"Slack history request failed for {channel_id}: {exc}"
                        ) from exc
                    if resp.status_code != 200:
                        raise ConnectionError(
                            f"Slack conversations.history returned HTTP "
                            f"{resp.status_code} for {channel_id}: {resp.text[:200]}"
                        )

                    payload = resp.json()
                    if not payload.get("ok"):
                        raise ConnectionError(
                            f"Slack conversations.history failed for {channel_id}: "
                            f"{payload.get('error', 'unknown_error')}"
                        )

                    for message in payload.get("messages", []) or []:
                        if len(memories) >= self._max_messages:
                            break
                        memory = self._to_memory(channel_id, message)
                        if memory is not None:
                            memories.append(memory)

                    next_cursor = (
                        (payload.get("response_metadata") or {}).get("next_cursor", "")
                        or ""
                    ).strip()
                    if not next_cursor:
                        break
                    cursor = next_cursor

                if len(memories) >= self._max_messages:
                    break

        return memories

    def _to_memory(self, channel_id: str, message: dict) -> dict | None:
        text = str(message.get("text", "") or "").strip()
        if not text:
            return None

        ts = str(message.get("ts", "") or "")
        captured_at = ""
        if ts:
            try:
                captured_at = datetime.fromtimestamp(
                    float(ts), tz=timezone.utc
                ).isoformat()
            except (TypeError, ValueError, OverflowError):
                captured_at = ""

        user_id = str(message.get("user", "") or message.get("bot_id", "") or "")
        thread_ts = str(message.get("thread_ts", "") or "")
        tags = ["slack", "message", f"channel:{channel_id}"]
        if thread_ts:
            tags.append("thread")

        return {
            "content": text,
            "tags": tags,
            "metadata": {
                "source": "slack",
                "type": "message",
                "channel_id": channel_id,
                "user_id": user_id,
                "ts": ts,
                "thread_ts": thread_ts,
                "reply_count": int(message.get("reply_count", 0) or 0),
                "team_id": self._team_id,
                "team_name": self._team_name,
                "captured_at": captured_at,
            },
        }

    async def disconnect(self) -> None:
        """Nothing to release: requests use short-lived clients."""
        self._headers = {}
