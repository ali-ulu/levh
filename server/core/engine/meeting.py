"""Meeting preparation.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations


from .helpers import _COMMITMENT_PATTERN, _event_date, _event_when, _first_marker_sentence
from ..types import (
    Memory,
)


class MemoryMeetingMixin:
    """Meeting preparation."""

    async def meeting_prep(
        self, query: str = "", within_days: int = 14, max_people: int = 8
    ) -> dict:
        """Prepare for a meeting — the proactive "before you walk in" brief.

        Picks the next upcoming meeting (an event with attendees, or a
        calendar/transcript memory dated in the future) — or, if ``query`` is
        given, the best-matching meeting — then assembles, deterministically
        and offline:

          - the meeting itself (title, time, attendees, project);
          - for each attendee, what you last discussed with them (recent
            memories mentioning them, newest first);
          - open commitments relevant to the meeting (same project, or
            mentioning an attendee by name);
          - recent decisions relevant to the meeting's project.

        Args:
            query: Optional text to match a specific meeting instead of the
                next upcoming one.
            within_days: How far ahead to look for the next meeting. Default 14.
            max_people: Cap on attendees to build context for. Default 8.
        """
        from datetime import datetime, timedelta, timezone

        from ..people import extract_people

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        horizon_iso = (now + timedelta(days=max(within_days, 1))).isoformat()

        memories = await self.episodic.search(limit=10000)

        def _is_meeting(m: Memory) -> bool:
            md = m.metadata or {}
            if isinstance(md.get("attendees"), list) and md.get("attendees"):
                return True
            src = m.source or ""
            return "calendar" in src or "transcript" in src

        meetings = [m for m in memories if _is_meeting(m)]

        chosen: Memory | None = None
        if query:
            q = query.strip().lower()
            matches = [
                m
                for m in meetings
                if q in (m.content or "").lower()
                or q in str((m.metadata or {}).get("title", "")).lower()
            ]
            upcoming = sorted(
                [m for m in matches if _event_when(m) >= now_iso], key=_event_when
            )
            if upcoming:
                chosen, reason = upcoming[0], "matched query (upcoming)"
            elif matches:
                chosen = sorted(matches, key=_event_when, reverse=True)[0]
                reason = "matched query (most recent)"
            else:
                reason = f"no meeting matching '{query}'"
        else:
            upcoming = sorted(
                [m for m in meetings if now_iso <= _event_when(m) <= horizon_iso],
                key=_event_when,
            )
            if upcoming:
                chosen, reason = upcoming[0], "next upcoming meeting"
            else:
                reason = f"no upcoming meetings in the next {within_days} days"

        if chosen is None:
            self._emit("meeting_prepped", {"found": False})
            return {
                "generated_at": now_iso,
                "meeting": None,
                "reason": reason,
                "people": [],
                "open_commitments": [],
                "recent_decisions": [],
            }

        md = chosen.metadata or {}
        # Deduplicate attendees by identity key (email, else lowercased name).
        seen: set[str] = set()
        attendees: list[tuple[str, str, str]] = []
        for name, email in extract_people(md):
            key = email or name.lower()
            if key in seen:
                continue
            seen.add(key)
            attendees.append((key, name, email))

        when_raw = _event_when(chosen)
        meeting = {
            "id": chosen.id,
            "title": (str(md.get("title") or "").strip() or chosen.content.split("\n", 1)[0])[:140],
            "when": when_raw[:16].replace("T", " ") if when_raw else "",
            "project": chosen.project,
            "source": chosen.source,
            "attendees": [name for _, name, _ in attendees],
        }

        # Per-attendee: the memories (other than this meeting) that mention them.
        people_ctx = []
        attendee_names_lower = [name.lower() for _, name, _ in attendees]
        for key, name, email in attendees[:max_people]:
            hits = []
            for m in memories:
                if m.id == chosen.id:
                    continue
                found = extract_people(m.metadata or {})
                if any((e or n.lower()) == key for n, e in found):
                    hits.append(m)
            hits.sort(key=lambda m: m.created_at or "", reverse=True)
            recent = [
                {
                    "id": m.id,
                    "summary": m.content.split("\n", 1)[0][:120],
                    "date": _event_date(m),
                }
                for m in hits[:3]
            ]
            people_ctx.append(
                {
                    "name": name,
                    "email": email or None,
                    "last_seen": (hits[0].created_at or "")[:10] if hits else "",
                    "interaction_count": len(hits),
                    "recent": recent,
                }
            )

        # Open commitments relevant to this meeting: same project if the
        # meeting has one, else those naming an attendee. Excludes the meeting
        # memory itself and de-dups by extracted sentence.
        open_commitments = []
        seen_text: set[str] = set()
        for m in memories:
            if m.id == chosen.id:
                continue
            sentence = _first_marker_sentence(m.content or "", _COMMITMENT_PATTERN)
            if not sentence:
                continue
            relevant = False
            if chosen.project and m.project == chosen.project:
                relevant = True
            elif attendee_names_lower and any(
                n and n in (m.content or "").lower() for n in attendee_names_lower
            ):
                relevant = True
            if not relevant or sentence in seen_text:
                continue
            seen_text.add(sentence)
            open_commitments.append(
                {
                    "id": m.id,
                    "text": sentence,
                    "date": _event_date(m),
                    "project": m.project,
                }
            )
            if len(open_commitments) >= 15:
                break
        open_commitments.sort(key=lambda c: c["date"], reverse=True)

        # Recent decisions relevant to the meeting's project (or global).
        recent_decisions = await self.list_decisions(
            project=chosen.project, days=90, limit=10
        )

        self._emit("meeting_prepped", {"found": True, "meeting_id": chosen.id})
        return {
            "generated_at": now_iso,
            "meeting": meeting,
            "reason": reason,
            "people": people_ctx,
            "open_commitments": open_commitments,
            "recent_decisions": recent_decisions,
        }
