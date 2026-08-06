"""People aggregation — turn captured metadata into a person graph.

The capture connectors already record people structurally:
  - calendar: metadata.attendees[], metadata.organizer
  - email:    metadata.from, metadata.to[], metadata.cc[]
  - transcript: metadata.speakers[]

This module rolls those up into distinct people — "who appears across my
memories, how often, from which sources, and in which memories" — so the
dashboard / MCP can answer "what do I know about X?" and "when did I last
interact with X?". Pure functions, no I/O, so it's trivially testable.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# Fields whose values are single people vs. lists of people.
_SINGLE_FIELDS = ("organizer", "from")
_LIST_FIELDS = ("attendees", "to", "cc", "speakers")

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_ANGLE_RE = re.compile(r"^\s*(.*?)\s*<([^>]+)>\s*$")


def parse_person(raw: str) -> Optional[tuple[str, str]]:
    """Parse a person string into (display_name, email).

    Handles "Name <email>", a bare email, or a bare name. Returns None for
    empty/garbage. Either field may be "" but not both."""
    if not raw or not raw.strip():
        return None
    s = raw.strip()
    m = _ANGLE_RE.match(s)
    if m:
        name = m.group(1).strip().strip('"')
        email = m.group(2).strip().lower()
        if not name and email:
            name = email.split("@")[0]
        return (name, email)
    # bare email?
    if _EMAIL_RE.fullmatch(s):
        return (s.split("@")[0], s.lower())
    # an email somewhere inside?
    em = _EMAIL_RE.search(s)
    if em:
        email = em.group(0).lower()
        name = s.replace(em.group(0), "").strip().strip("<>").strip() or email.split("@")[0]
        return (name, email)
    return (s, "")


def _key(name: str, email: str) -> str:
    """Stable identity key: email when present (people rename), else lower name."""
    return email or name.lower()


def extract_people(metadata: dict) -> list[tuple[str, str]]:
    """All (name, email) people referenced in one memory's metadata."""
    if not isinstance(metadata, dict):
        return []
    out: list[tuple[str, str]] = []
    for field in _SINGLE_FIELDS:
        val = metadata.get(field)
        if isinstance(val, str):
            p = parse_person(val)
            if p:
                out.append(p)
    for field in _LIST_FIELDS:
        val = metadata.get(field)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    p = parse_person(item)
                    if p:
                        out.append(p)
    return out


def aggregate_people(memories: Iterable[Any]) -> list[dict]:
    """Roll a set of memories up into distinct people.

    ``memories`` are objects with ``.id``, ``.metadata``, ``.source``,
    ``.created_at`` (Memory models or anything duck-typed the same). Returns a
    list of person dicts sorted by memory_count desc, then name.
    """
    # Imported here, not at module scope: ``text_entities`` reuses
    # ``parse_person`` from this module, so a top-level import would cycle.
    from .text_entities import people_in_memory

    people: dict[str, dict] = {}
    for mem in memories:
        found = people_in_memory(mem)
        if not found:
            continue
        source = getattr(mem, "source", None)
        created = getattr(mem, "created_at", "") or ""
        # Deduplicate people within a single memory so one memory counts once.
        seen_in_mem: set[str] = set()
        for name, email in found:
            key = _key(name, email)
            if key in seen_in_mem:
                continue
            seen_in_mem.add(key)
            entry = people.get(key)
            if entry is None:
                entry = {
                    "key": key,
                    "name": name,
                    "email": email or None,
                    "memory_count": 0,
                    "memory_ids": [],
                    "sources": set(),
                    "last_seen": "",
                }
                people[key] = entry
            entry["memory_count"] += 1
            entry["memory_ids"].append(getattr(mem, "id", None))
            # Keep the most descriptive display name (longest, prefers spaces).
            if len(name) > len(entry["name"]):
                entry["name"] = name
            if source:
                entry["sources"].add(source)
            if created > entry["last_seen"]:
                entry["last_seen"] = created

    result = []
    for entry in people.values():
        entry["sources"] = sorted(entry["sources"])
        result.append(entry)
    result.sort(key=lambda e: (-e["memory_count"], e["name"].lower()))
    return result


def find_person_key(people: list[dict], query: str) -> Optional[str]:
    """Resolve a free-text query to a person key: exact key/email, then a
    case-insensitive name/email substring match (first, most-frequent)."""
    q = query.strip().lower()
    if not q:
        return None
    for p in people:
        if p["key"] == q or (p.get("email") or "").lower() == q:
            return p["key"]
    for p in people:  # people is already sorted by frequency
        if q in p["name"].lower() or q in (p.get("email") or "").lower():
            return p["key"]
    return None
