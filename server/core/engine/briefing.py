"""The daily briefing.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations


from .helpers import _event_date


class MemoryBriefingMixin:
    """The daily briefing."""

    async def briefing(self, project: str | None = None, days: int = 7) -> dict:
        """Deterministic "Daily Briefing": what's on today, what you recently
        committed to, and what you're about to forget. No LLM call — every
        section is computed from stored metadata/content so results are
        reproducible offline.

        Uses ``metadata.captured_at`` (when a calendar event or email
        actually happened) over ``created_at`` — same convention as
        ``timeline()`` — for both the "today" and "commitments" windows.

        Args:
            project: Optional project filter.
            days: Lookback window (in days) for the commitments/recent-count
                sections. Default 7.
        """
        from datetime import datetime, timedelta, timezone
        import re

        days = min(max(days, 1), 90)
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        cutoff = (now - timedelta(days=days)).date().isoformat()

        memories = await self.episodic.search(project=project, limit=10000)

        # ── Today: memories whose event date is today ──────────────
        today_items = []
        for m in memories:
            if _event_date(m) != today:
                continue
            captured_at = (m.metadata or {}).get("captured_at") or ""
            time_part = captured_at[11:16] if "T" in captured_at else ""
            today_items.append(
                {
                    "id": m.id,
                    "summary": m.content.split("\n", 1)[0][:120],
                    "source": m.source,
                    "time": time_part,
                }
            )
        today_items.sort(key=lambda it: (it["time"] == "", it["time"]))

        # ── Commitments: open action items in recent memories ───────
        marker_pattern = re.compile(
            r"\bI['’]?ll\b|\bI will\b|\bwe['’]ll\b|\bwe will\b|\bgoing to\b|"
            r"\bneed to\b|\bTODO\b|\baction item\b|\bfollow[- ]?up\b|"
            r"yapacağ|göndereceğ|halledeceğ|takip ed",
            re.IGNORECASE,
        )

        recent = [m for m in memories if _event_date(m) and _event_date(m) >= cutoff]
        recent.sort(key=lambda m: _event_date(m), reverse=True)

        commitments: list[dict] = []
        seen_text: set[str] = set()
        for m in recent:
            content = m.content or ""
            if not marker_pattern.search(content):
                continue
            segments: list[str] = []
            for line in content.split("\n"):
                segments.extend(line.split(". "))
            sentence = next((s for s in segments if marker_pattern.search(s)), content)
            text = sentence.strip()[:160]
            if not text or text in seen_text:
                continue
            seen_text.add(text)
            commitments.append(
                {
                    "id": m.id,
                    "text": text,
                    "source": m.source,
                    "date": _event_date(m),
                    "project": m.project,
                }
            )
            if len(commitments) >= 30:
                break

        # ── Fading: reuse the existing "about to be forgotten" logic ─
        fading_pairs = await self.list_fading(threshold=0.5, project=project, limit=5)
        fading = [
            {
                "id": m.id,
                "summary": m.content.split("\n", 1)[0][:120],
                "retention": round(retention, 4),
            }
            for m, retention in fading_pairs
        ]

        self._emit("briefed", {"project": project, "recent_total": len(recent)})
        return {
            "generated_at": now.isoformat(),
            "today": today_items,
            "commitments": commitments,
            "fading": fading,
            "counts": {
                "today": len(today_items),
                "commitments": len(commitments),
                "fading": len(fading),
                "recent_total": len(recent),
            },
        }
