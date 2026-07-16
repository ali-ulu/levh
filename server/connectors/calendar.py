"""Calendar Connector — Import calendar events as memories.

The first "real work-life capture" source (roadmap Phase 1): your meetings
become memories automatically, so later you can ask "what did I discuss with
X last week?" or get a pre-meeting brief.

Parses **iCalendar (.ics)** — the universal format Google Calendar, Outlook,
and Apple Calendar all export and publish. This is done with a small,
dependency-free parser (no `icalendar` package) so the connector works fully
offline and never drags in heavy deps, matching the project's philosophy.

Config keys (at least one of ics_path / ics_url required):
    ics_path (str):  Path to a local .ics file (exported or synced calendar).
    ics_url (str):   URL of a published/subscribed .ics feed (fetched via httpx).
    past_days (int, optional):   Only import events starting within the last N
        days. 0/omit = no lower bound.
    future_days (int, optional): Only import events starting within the next N
        days. 0/omit = no upper bound.
    calendar_name (str, optional): Label stored on each event's metadata.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .base import BaseConnector


# ── Minimal iCalendar parser (RFC 5545 subset) ───────────────────────


def _unfold(text: str) -> list[str]:
    """RFC 5545 line unfolding: a line starting with space/tab continues the
    previous one. Returns logical lines."""
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines: list[str] = []
    for line in raw:
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _split_prop(line: str) -> tuple[str, dict[str, str], str]:
    """Split 'NAME;PARAM=v:VALUE' into (name, params, value)."""
    if ":" not in line:
        return line.strip().upper(), {}, ""
    head, value = line.split(":", 1)
    parts = head.split(";")
    name = parts[0].strip().upper()
    params: dict[str, str] = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.strip().upper()] = v.strip()
    return name, params, value


def _unescape(value: str) -> str:
    """Reverse RFC 5545 text escaping."""
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _parse_dt(value: str, params: dict[str, str]) -> Optional[datetime]:
    """Parse an iCalendar DATE or DATE-TIME into an aware UTC datetime.

    Handles: 20260115T143000Z (UTC), 20260115T143000 (floating/local -> UTC),
    and 20260115 (all-day date). Timezone names (TZID param) are treated as
    naive-local promoted to UTC — good enough for recall/briefing, and never
    raises on an unknown zone."""
    v = value.strip()
    try:
        if len(v) == 8 and params.get("VALUE") == "DATE" or (len(v) == 8 and "T" not in v):
            return datetime.strptime(v, "%Y%m%d").replace(tzinfo=timezone.utc)
        if v.endswith("Z"):
            return datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        # Floating or TZID local time — promote to UTC without a tz database.
        return datetime.strptime(v, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_ics(text: str) -> list[dict]:
    """Parse VEVENT blocks from iCalendar text into structured event dicts.

    Returns a list of dicts: summary, description, location, start, end,
    attendees[], organizer, uid. Malformed events are skipped, never raised."""
    events: list[dict] = []
    current: Optional[dict] = None
    for line in _unfold(text):
        upper = line.strip().upper()
        if upper == "BEGIN:VEVENT":
            current = {"attendees": []}
            continue
        if upper == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
            continue
        if current is None:
            continue

        name, params, value = _split_prop(line)
        if name == "SUMMARY":
            current["summary"] = _unescape(value)
        elif name == "DESCRIPTION":
            current["description"] = _unescape(value)
        elif name == "LOCATION":
            current["location"] = _unescape(value)
        elif name == "DTSTART":
            current["start"] = _parse_dt(value, params)
        elif name == "DTEND":
            current["end"] = _parse_dt(value, params)
        elif name == "UID":
            current["uid"] = value.strip()
        elif name == "ATTENDEE":
            person = params.get("CN") or value.replace("mailto:", "").strip()
            if person:
                current["attendees"].append(person)
        elif name == "ORGANIZER":
            current["organizer"] = params.get("CN") or value.replace("mailto:", "").strip()
    return events


class CalendarConnector(BaseConnector):
    """Import calendar events (.ics) as memories."""

    name: str = "calendar"
    description: str = (
        "Import calendar events from an iCalendar (.ics) file or published URL "
        "(Google/Outlook/Apple export). Each event becomes a memory with its "
        "title, time, attendees, and location — so meetings are captured "
        "automatically. Fully offline for file paths; no API keys needed."
    )

    def __init__(self) -> None:
        self._text: str = ""
        self._past_days: int = 0
        self._future_days: int = 0
        self._calendar_name: str = ""

    def required_config_keys(self) -> list[str]:
        # Primary key shown in the dashboard form. ics_url is an alternative,
        # documented in help_text and accepted by connect().
        return ["ics_path"]

    def help_text(self) -> str:
        return (
            "Connector: calendar\n"
            "  Import calendar events (.ics) as memories.\n"
            "  Provide ONE of:\n"
            "    ics_path : path to a local .ics file (exported/synced calendar)\n"
            "    ics_url  : URL of a published/subscribed .ics feed\n"
            "  Optional: past_days, future_days (window filter), calendar_name.\n"
            "  Tip: Google Calendar → Settings → Export; Outlook → Publish/Share ics."
        )

    async def connect(self, config: dict) -> bool:
        """Load the ICS text from a file path or URL."""
        ics_path = config.get("ics_path", "").strip()
        ics_url = config.get("ics_url", "").strip()
        self._past_days = int(config.get("past_days", 0) or 0)
        self._future_days = int(config.get("future_days", 0) or 0)
        self._calendar_name = config.get("calendar_name", "").strip()

        if ics_path:
            if not os.path.isfile(ics_path):
                raise FileNotFoundError(f"Calendar file not found: {ics_path}")
            with open(ics_path, "r", encoding="utf-8", errors="replace") as f:
                self._text = f.read()
        elif ics_url:
            import httpx

            try:
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    resp = await client.get(ics_url)
                    resp.raise_for_status()
                    self._text = resp.text
            except httpx.HTTPError as e:
                raise ConnectionError(f"Could not fetch calendar URL: {e}")
        else:
            raise ValueError(
                "Calendar connector needs 'ics_path' (local .ics file) or "
                "'ics_url' (published .ics feed) in config."
            )
        return True

    def _within_window(self, start: Optional[datetime]) -> bool:
        if start is None:
            return True  # undated events (rare) are kept
        now = datetime.now(timezone.utc)
        if self._past_days and start < now - timedelta(days=self._past_days):
            return False
        if self._future_days and start > now + timedelta(days=self._future_days):
            return False
        return True

    @staticmethod
    def _format_event(ev: dict, calendar_name: str) -> Optional[dict]:
        """Turn a parsed event into a memory-compatible dict."""
        summary = (ev.get("summary") or "").strip()
        if not summary:
            return None

        start: Optional[datetime] = ev.get("start")
        when = start.strftime("%Y-%m-%d %H:%M UTC") if start else "unknown time"
        attendees = ev.get("attendees") or []
        organizer = ev.get("organizer")
        location = (ev.get("location") or "").strip()
        description = (ev.get("description") or "").strip()

        parts = [f"Meeting: {summary}", f"When: {when}"]
        people = []
        if organizer:
            people.append(f"{organizer} (organizer)")
        people.extend(a for a in attendees if a and a != organizer)
        if people:
            parts.append(f"With: {', '.join(people)}")
        if location:
            parts.append(f"Where: {location}")
        if description:
            parts.append(f"Notes: {description}")
        content = "\n".join(parts)

        tags = ["calendar", "meeting"]
        metadata: dict[str, Any] = {
            "event_uid": ev.get("uid"),
            "start": start.isoformat() if start else None,
            "end": ev["end"].isoformat() if ev.get("end") else None,
            "attendees": attendees,
            "organizer": organizer,
            "location": location or None,
            "calendar": calendar_name or None,
        }
        # captured_at reflects when the event actually happens, not import time.
        if start:
            metadata["captured_at"] = start.isoformat()
        return {"content": content, "tags": tags, "metadata": metadata}

    async def fetch(self, **kwargs: Any) -> list[dict]:
        """Parse the loaded ICS and return events (within window) as memories."""
        if not self._text:
            raise RuntimeError("Not connected. Call connect() first.")

        memories: list[dict] = []
        for ev in parse_ics(self._text):
            if not self._within_window(ev.get("start")):
                continue
            formatted = self._format_event(ev, self._calendar_name)
            if formatted:
                memories.append(formatted)

        # Deterministic order: soonest first (undated last).
        memories.sort(
            key=lambda m: m["metadata"].get("start") or "9999"
        )
        return memories

    async def disconnect(self) -> None:
        self._text = ""
