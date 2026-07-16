"""Email Connector — Import email as memories (Phase 1 work-life capture).

The second real capture source after Calendar. Your important emails become
memories — sender, recipients, subject, date, and a body excerpt — so later you
can ask "what did Dana email me about pricing?" or "what did I promise in that
thread?".

Parses standard offline formats with the Python standard library (zero extra
deps, matching the project philosophy):
  - ``.mbox``  — the universal mailbox export (Gmail Takeout, Thunderbird,
    Apple Mail, Outlook-via-export all produce mbox).
  - ``.eml``   — a single RFC 822 message (drag-and-drop a message to disk).
  - a directory of ``.eml`` files.

No IMAP/OAuth here on purpose: file-based import is fully offline, deterministic,
and needs no credentials. Live Gmail/Outlook API sync is a later step.

Config keys (provide one of mbox_path / eml_path / eml_dir):
    mbox_path (str): Path to a .mbox file.
    eml_path (str):  Path to a single .eml file.
    eml_dir (str):   Path to a directory containing .eml files.
    past_days (int, optional):   Only import messages from the last N days.
    future_days (int, optional): Rarely useful for mail; supported for symmetry.
    max_messages (int, optional): Cap the number imported (default 500).
    body_chars (int, optional):  Body excerpt length (default 600, 0 = no body).
    exclude_senders (list[str], optional): Skip messages whose From matches any
        of these substrings (e.g. ["no-reply", "notifications@"]).
"""

from __future__ import annotations

import email
import mailbox
import os
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any, Optional

from .base import BaseConnector

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]*\n[ \t\n]*")


def _decode(value: Optional[str]) -> str:
    """Decode an RFC 2047 encoded header into a plain str. Never raises."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _format_person(name: str, addr: str) -> str:
    name = name.strip()
    addr = addr.strip()
    if name and addr:
        return f"{name} <{addr}>"
    return name or addr


def _people(msg: Message, header: str) -> list[str]:
    raw = msg.get_all(header, [])
    out: list[str] = []
    for name, addr in getaddresses(raw):
        person = _format_person(_decode(name), addr)
        if person:
            out.append(person)
    return out


def _plain_body(msg: Message, limit: int) -> str:
    """Best-effort plain-text body: prefer text/plain, else strip HTML.
    Skips attachments. Truncates to ``limit`` chars."""
    if limit <= 0:
        return ""

    def _decode_part(part: Message) -> str:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except (LookupError, TypeError):
            return payload.decode("utf-8", errors="replace")

    text = ""
    html = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain" and not text:
                text = _decode_part(part)
            elif ctype == "text/html" and not html:
                html = _decode_part(part)
    else:
        ctype = msg.get_content_type()
        if ctype == "text/html":
            html = _decode_part(msg)
        else:
            text = _decode_part(msg)

    body = text or _HTML_TAG_RE.sub(" ", html)
    body = _WS_RE.sub("\n", body).strip()
    if len(body) > limit:
        body = body[:limit].rsplit(" ", 1)[0] + "…"
    return body


class EmailConnector(BaseConnector):
    """Import email (.mbox / .eml) as memories."""

    name: str = "email"
    description: str = (
        "Import email from a .mbox file, a single .eml, or a folder of .eml "
        "files (Gmail Takeout, Thunderbird, Apple Mail, Outlook export). Each "
        "message becomes a memory with sender, recipients, subject, date, and a "
        "body excerpt. Fully offline, no IMAP/OAuth, no API keys."
    )

    def __init__(self) -> None:
        self._messages: list[Message] = []
        self._past_days: int = 0
        self._future_days: int = 0
        self._max_messages: int = 500
        self._body_chars: int = 600
        self._exclude_senders: list[str] = []

    def required_config_keys(self) -> list[str]:
        # Primary key shown in the dashboard form; eml_path / eml_dir are
        # alternatives documented in help_text and accepted by connect().
        return ["mbox_path"]

    def help_text(self) -> str:
        return (
            "Connector: email\n"
            "  Import email (.mbox / .eml) as memories.\n"
            "  Provide ONE of:\n"
            "    mbox_path : path to a .mbox file (Gmail Takeout, Thunderbird…)\n"
            "    eml_path  : path to a single .eml message\n"
            "    eml_dir   : path to a folder of .eml files\n"
            "  Optional: past_days, max_messages, body_chars, exclude_senders.\n"
            "  Tip: Gmail → Takeout → Mail exports .mbox; drag a message to disk for .eml."
        )

    async def connect(self, config: dict) -> bool:
        mbox_path = config.get("mbox_path", "").strip()
        eml_path = config.get("eml_path", "").strip()
        eml_dir = config.get("eml_dir", "").strip()
        self._past_days = int(config.get("past_days", 0) or 0)
        self._future_days = int(config.get("future_days", 0) or 0)
        self._max_messages = int(config.get("max_messages", 500) or 500)
        self._body_chars = int(config.get("body_chars", 600) or 0)
        self._exclude_senders = [
            s.lower() for s in (config.get("exclude_senders") or []) if s
        ]

        self._messages = []
        if mbox_path:
            if not os.path.isfile(mbox_path):
                raise FileNotFoundError(f"Mailbox file not found: {mbox_path}")
            box = mailbox.mbox(mbox_path)
            self._messages = [email.message_from_string(str(m)) for m in box]
        elif eml_path:
            if not os.path.isfile(eml_path):
                raise FileNotFoundError(f"Email file not found: {eml_path}")
            with open(eml_path, "rb") as f:
                self._messages = [email.message_from_binary_file(f)]
        elif eml_dir:
            if not os.path.isdir(eml_dir):
                raise FileNotFoundError(f"Email directory not found: {eml_dir}")
            for name in sorted(os.listdir(eml_dir)):
                if not name.lower().endswith(".eml"):
                    continue
                with open(os.path.join(eml_dir, name), "rb") as f:
                    self._messages.append(email.message_from_binary_file(f))
        else:
            raise ValueError(
                "Email connector needs 'mbox_path', 'eml_path', or 'eml_dir' in config."
            )
        return True

    def _msg_date(self, msg: Message) -> Optional[datetime]:
        raw = msg.get("Date")
        if not raw:
            return None
        try:
            dt = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _within_window(self, dt: Optional[datetime]) -> bool:
        if dt is None:
            return True
        now = datetime.now(timezone.utc)
        if self._past_days and dt < now - timedelta(days=self._past_days):
            return False
        if self._future_days and dt > now + timedelta(days=self._future_days):
            return False
        return True

    def _excluded(self, sender: str) -> bool:
        low = sender.lower()
        return any(x in low for x in self._exclude_senders)

    def _format(self, msg: Message) -> Optional[dict]:
        subject = _decode(msg.get("Subject")).strip() or "(no subject)"
        senders = _people(msg, "From")
        sender = senders[0] if senders else ""
        if sender and self._excluded(sender):
            return None
        to = _people(msg, "To")
        cc = _people(msg, "Cc")
        dt = self._msg_date(msg)
        when = dt.strftime("%Y-%m-%d %H:%M UTC") if dt else "unknown date"
        body = _plain_body(msg, self._body_chars)

        parts = [f"Email: {subject}", f"From: {sender or 'unknown'}", f"When: {when}"]
        recipients = to + cc
        if recipients:
            parts.append(f"To: {', '.join(recipients)}")
        if body:
            parts.append(f"Body: {body}")
        content = "\n".join(parts)

        metadata: dict[str, Any] = {
            "subject": subject,
            "from": sender or None,
            "to": to,
            "cc": cc,
            "date": dt.isoformat() if dt else None,
            "message_id": (msg.get("Message-ID") or "").strip() or None,
        }
        if dt:
            metadata["captured_at"] = dt.isoformat()
        return {"content": content, "tags": ["email"], "metadata": metadata}

    async def fetch(self, **kwargs: Any) -> list[dict]:
        if not self._messages:
            # Empty mailbox is valid (returns nothing); only unconnected is an error.
            return []

        memories: list[dict] = []
        for msg in self._messages:
            if len(memories) >= self._max_messages:
                break
            dt = self._msg_date(msg)
            if not self._within_window(dt):
                continue
            formatted = self._format(msg)
            if formatted:
                memories.append(formatted)

        # Most recent first (undated last).
        memories.sort(key=lambda m: m["metadata"].get("date") or "", reverse=True)
        return memories

    async def disconnect(self) -> None:
        self._messages = []
