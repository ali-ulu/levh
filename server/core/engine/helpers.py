"""Small helpers shared by the MemoryEngine mixins.

They live here rather than in memory_engine.py because the mixins would
otherwise have to import from the module that imports them.
"""

from __future__ import annotations

import logging
import re

from typing import Callable

from ..types import Memory

# Engine event fan-out signature: (event name, payload).
EventListener = Callable[[str, dict], None]


def _event_date(m: Memory) -> str:
    """Event date for a memory: ``metadata.captured_at`` (when a calendar
    event or email actually happened) over ``created_at`` (when it was
    captured into LEVH), truncated to ``YYYY-MM-DD``. Shared by
    ``timeline()``, ``briefing()``, and ``list_decisions()`` so "when did
    this happen" is answered identically everywhere."""
    captured_at = (m.metadata or {}).get("captured_at")
    day_source = captured_at if captured_at else m.created_at
    return (day_source or "")[:10]


def _event_when(m: Memory) -> str:
    """Full event timestamp for a memory (ISO): ``metadata.captured_at`` when
    present, else ``created_at``. Used by ``meeting_prep`` to order events on
    the clock, not just the day."""
    return ((m.metadata or {}).get("captured_at") or m.created_at or "")


# Commitment / open-action-item markers (English + Turkish). Shared by the
# meeting-prep relevance filter; ``briefing()`` keeps its own copy so its
# tested behaviour is insulated from changes here.
_COMMITMENT_PATTERN = re.compile(
    r"\bI['’]?ll\b|\bI will\b|\bwe['’]ll\b|\bwe will\b|\bgoing to\b|"
    r"\bneed to\b|\bTODO\b|\baction item\b|\bfollow[- ]?up\b|"
    r"yapacağ|göndereceğ|halledeceğ|takip ed",
    re.IGNORECASE,
)


def _first_marker_sentence(content: str, pattern: "re.Pattern", limit: int = 160) -> str | None:
    """Return the first sentence in ``content`` that matches ``pattern``
    (splitting on newlines then ``". "``), trimmed to ``limit`` chars, or
    None if nothing matches."""
    if not content or not pattern.search(content):
        return None
    segments: list[str] = []
    for line in content.split("\n"):
        segments.extend(line.split(". "))
    sentence = next((s for s in segments if pattern.search(s)), content)
    return sentence.strip()[:limit] or None


logger = logging.getLogger("levh.memory_engine")
