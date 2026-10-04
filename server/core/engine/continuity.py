"""Context handed to the next session: context files and continuity.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from ..types import RULE_TAG, DECISION_TAG, BLOCKER_TAG
from ..types import Memory

logger = logging.getLogger(__name__)


class MemoryContinuityMixin:
    """Context handed to the next session: context files and continuity."""

    async def record_brief_emission(
        self,
        channel: str,
        surfaced_ids: list[str],
        project: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Record that a brief was handed out on ``channel`` (#378).

        The engine method exists so every emitter — the MCP stderr bridge,
        the MCP tool, the CLI ``continue`` command the session hook calls —
        goes through one write path, the same way recall logging does. It
        records the EMISSION, not a delivery: the name is the contract, and
        no counter here may be read as "the agent read the brief".

        Best-effort by design: an instrumentation failure must never be the
        thing that stops a session from getting its brief, so the failure is
        logged and reported instead of raised. Only the store failure the
        write can actually produce is caught — a programming error in this
        call path propagates, because a bug that silently loses the
        measurement is worse than a loud one.
        """
        try:
            return await self.db.continuity_log.record_brief_emission(
                channel=channel,
                surfaced_ids=list(surfaced_ids),
                project=project,
                session_id=session_id,
            )
        except sqlite3.Error as exc:
            logger.warning("continuity emission log failed: %s", exc)
            return {"logged": False}

    async def get_continuity_signals(
        self,
        task: str | None = None,  # noqa: ARG002
        project: str | None = None,
        limit: int = 5,
        since: str | None = None,
    ) -> dict:
        """The continuity brief's ingredients, structured.

        Issue #378: the text brief is what a human reads and what a client
        prints, but the *measurement* needs the machine-shaped answer — which
        checkpoint, which pinned memories, which decisions and blockers the
        brief is about to surface. Both the text brief and the fixture
        evaluator are built on this so "surfaced" means the same thing in
        both.

        The contract is deliberately narrow: nothing here records an emission
        (that is the emitter's job), and nothing here reinforces (a brief
        handed out is not a recall).
        """
        # Get recent memories for context
        recent_memories = await self.episodic.search(
            project=project,
            limit=50,
        )

        # Get recent sessions for the project. Session metadata rarely carries
        # a "project" key, so a session also counts as belonging to the project
        # when any of the project's memories were recorded under it.
        sessions = await self.list_sessions(limit=limit * 2)
        if project:
            project_session_ids = {m.session_id for m in recent_memories if m.session_id}
            sessions = [
                s
                for s in sessions
                if s.metadata.get("project") == project or s.id in project_session_ids
            ]

        # Filter by date if since provided — before truncating to `limit`,
        # otherwise the cut-off list is filtered instead of the full one.
        if since:
            def _as_utc(value: str) -> datetime:
                # A bare date like "2026-01-01" parses as naive and cannot be
                # compared against the tz-aware timestamps we store, so assume UTC.
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

            try:
                since_dt = _as_utc(since)
                sessions = [s for s in sessions if s.created_at and
                            _as_utc(s.created_at) >= since_dt]
            except ValueError:
                pass  # Invalid date format, ignore filter

        sessions = sessions[:limit]

        # Also get git-hook memories (commits) for the project
        commit_memories = [m for m in recent_memories if m.source == "git-hook"]
        if project:
            commit_memories = [m for m in commit_memories if m.project == project]

        # Pinned context: rules and notes come from the pin flag rather than
        # from keyword guessing, same as the text brief.
        pinned = [m for m in recent_memories if m.pinned]
        rules = [m for m in pinned if RULE_TAG in (m.tags or [])]
        notes = [m for m in pinned if RULE_TAG not in (m.tags or [])]

        decisions: list[Memory] = []
        for m in recent_memories[:30]:
            if DECISION_TAG in (m.tags or []):
                decisions.append(m)
                continue
            content_lower = m.content.lower()
            if any(kw in content_lower for kw in ["decided", "agreed", "karar", "seçtik", "we'll", "will use", "switching to"]):
                decisions.append(m)

        blockers: list[Memory] = []
        for m in recent_memories[:30]:
            if BLOCKER_TAG in (m.tags or []):
                blockers.append(m)
                continue
            content_lower = m.content.lower()
            if any(kw in content_lower for kw in ["error", "failed", "blocked", "todo", "fixme", "hata", "başarısız", "takıldı"]):
                blockers.append(m)

        checkpoint: Memory | None = None
        checkpoints = await self.agent_tracker.list_checkpoints(project=project, limit=1)
        if checkpoints:
            cp = checkpoints[0]
            checkpoint = Memory(
                id=f"checkpoint:{cp.get('id', '')}",
                content=cp.get("summary") or cp.get("title") or "",
                metadata={
                    "title": (cp.get("title") or "").strip(),
                    "agent_name": cp.get("agent_name") or "unknown",
                    "checkpoint_type": cp.get("checkpoint_type", "auto"),
                    "created_at": cp.get("created_at") or "",
                },
            )

        # Surfaced ids in presentation order: the checkpoint first (it is
        # "where did we leave off"), then rules, pinned notes, decisions and
        # blockers. This ordering IS the brief's ordering, so the use counter
        # computed from it describes the brief a client actually saw.
        surfaced_ids = [m.id for m in ([checkpoint] if checkpoint else [])]
        surfaced_ids += [r.id for r in sorted(rules, key=lambda m: m.importance, reverse=True)[:10]]
        surfaced_ids += [n.id for n in notes[:10]]
        surfaced_ids += [d.id for d in decisions[:5]]
        surfaced_ids += [b.id for b in blockers[:5]]

        return {
            "checkpoint": checkpoint,
            "rules": rules,
            "pinned_notes": notes,
            "sessions": sessions,
            "commit_memories": commit_memories,
            "decisions": decisions,
            "blockers": blockers,
            "surfaced_ids": surfaced_ids,
            "surfaced_checkpoint": checkpoint.id if checkpoint else None,
            "surfaced_rule_ids": [r.id for r in sorted(rules, key=lambda m: m.importance, reverse=True)[:10]],
            "surfaced_pinned_ids": [n.id for n in notes[:10]],
            "surfaced_decision_ids": [d.id for d in decisions[:5]],
            "surfaced_blocker_ids": [b.id for b in blockers[:5]],
        }

    async def generate_context_file(
        self,
        project: str | None = None,
        style: str = "claude",
        max_memories: int = 60,
        max_rules: int = 10,
    ) -> str:
        """Compile memories into a persistent context file for AI clients.

        Args:
            project: Only include this project's memories (None = all).
            style: "claude" (CLAUDE.md) or "cursor" (.cursorrules).
            max_memories: Cap on included memories.
            max_rules: Cap on guard rules listed in their own section.
        """
        pinned = await self.episodic.search(project=project, pinned=True, limit=max_memories)
        important = await self.episodic.search(
            project=project, min_importance=0.7, limit=max_memories
        )
        recent = await self.episodic.search(project=project, limit=15)

        seen: set[str] = set()

        def _dedup(memories: list[Memory]) -> list[Memory]:
            out = []
            for m in memories:
                if m.id not in seen:
                    out.append(m)
                    seen.add(m.id)
            return out

        pinned = _dedup(pinned)
        important = [m for m in _dedup(important) if not m.pinned]
        recent = _dedup(recent)

        # Rules recorded by the mistake guard are pinned too, so split them out
        # of the generic pinned list rather than printing them twice. They lead
        # the file: a rule exists because ignoring it already cost something.
        rules = [m for m in pinned if RULE_TAG in (m.tags or [])]
        rules.sort(key=lambda m: (m.importance, m.created_at), reverse=True)
        pinned = [m for m in pinned if RULE_TAG not in (m.tags or [])]

        title = f"Project Memory — {project}" if project else "Project Memory"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        lines: list[str] = []
        if style == "cursor":
            lines.append(f"# {title}")
            lines.append(f"# Generated by LEVH on {now}. Do not edit by hand.")
        else:
            lines.append(f"# {title}")
            lines.append("")
            lines.append(f"> Generated by LEVH on {now}. Do not edit by hand — ")
            lines.append("> update memories in LEVH and regenerate.")
        lines.append("")

        def _bullet(m: Memory) -> str:
            tags = f" _[{', '.join(m.tags)}]_" if m.tags else ""
            return f"- {m.content.strip()}{tags}"

        if rules:
            lines.append("## Rules Learned From Mistakes")
            lines.append("")
            lines.append("Each of these was recorded after the mistake it describes.")
            lines.append("")
            lines.extend(f"- {m.content.strip()}" for m in rules[:max_rules])
            lines.append("")
        if pinned:
            lines.append("## Always Remember (pinned)")
            lines.extend(_bullet(m) for m in pinned)
            lines.append("")
        if important:
            lines.append("## Key Decisions & Facts")
            lines.extend(_bullet(m) for m in important)
            lines.append("")
        if recent:
            lines.append("## Recent Context")
            lines.extend(_bullet(m) for m in recent[:10])
            lines.append("")

        if not (rules or pinned or important or recent):
            lines.append("_No memories stored yet._")

        return "\n".join(lines).rstrip() + "\n"

    async def get_continuity_context(
        self,
        task: str | None = None,
        project: str | None = None,
        limit: int = 5,
        since: str | None = None,
    ) -> str:
        """Synthesize a continuity brief from recent sessions and memories.

        Returns a human-readable brief showing where the user left off,
        including recent sessions, active files, decisions, and blockers.

        A pure builder: no emission is recorded here, so tests and read-side
        callers get the text without writing rows. Emitters wrap this in
        :meth:`emit_continuity_brief`, which is what records the hand-off.
        """
        signals = await self.get_continuity_signals(
            task=task, project=project, limit=limit, since=since
        )
        return self._render_brief(signals, task)

    async def emit_continuity_brief(
        self,
        channel: str,
        task: str | None = None,
        project: str | None = None,
        limit: int = 5,
        since: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """Build the brief AND record that it was handed out (#378).

        This is the one entry point emitters should call: the MCP stderr
        bridge (``channel="stderr_bridge"``), the MCP tool and resource
        (``channel="mcp_tool"``), and the CLI ``continue`` command the
        session hook runs (``channel="session_hook"``; a bare ``levh
        continue`` is ``channel="cli"``).

        Every call records a row, even when the brief came back empty — the
        producer-side event happened either way, and ``briefs_with_content``
        separates the content-bearing subset from the empty ones. The row is
        an EMISSION record, not a delivery receipt: the name is the contract
        (issue #378), and no counter derived from it may be read as "the
        agent read the brief".

        Returns the brief text so the emitter can print or serve it.
        """
        signals = await self.get_continuity_signals(
            task=task, project=project, limit=limit, since=since
        )
        text = self._render_brief(signals, task)
        await self.record_brief_emission(
            channel=channel,
            surfaced_ids=signals["surfaced_ids"],
            project=project,
            session_id=session_id,
        )
        return text

    def _render_brief(self, signals: dict, task: str | None) -> str:
        """Assemble the text brief from structured signals.

        Sync and pure: every input arrives computed, so the text is a
        deterministic function of the store state ``get_continuity_signals``
        read.
        """
        sessions = signals["sessions"]
        commit_memories = signals["commit_memories"]

        # Build the brief. `lines` holds only the header until a section adds
        # something, which is how the empty case is detected below — a brief
        # that is all frame and no content is noise, and this one is injected
        # into every new session.
        lines = []
        lines.append("=== LEVH Continuity Brief ===")
        lines.append("")
        header_only = len(lines)

        if task:
            lines.append(f"Task: {task}")
            lines.append("")

        # Last checkpoint answers "where did we leave off" directly, so it
        # leads the brief — before rules/pinned, which are standing context
        # rather than "what just happened". Without this section the brief
        # only offered a session list with memory counts, forcing a manual
        # list_checkpoints call (or the user asking) to find the actual
        # last-session recap.
        if signals["checkpoint"]:
            cp = signals["checkpoint"]
            lines.append("Last Checkpoint:")
            summary = cp.content.strip()
            title = (cp.metadata.get("title") or "").strip()
            lines.append(f"  [{cp.metadata.get('checkpoint_type', 'auto')}] {title}")
            if summary and summary != title:
                lines.append(f"  {summary}")
            lines.append(f"  ({cp.metadata.get('agent_name', 'unknown')} · {cp.metadata.get('created_at', '')})")
            lines.append("")

        # Rules and pinned memories come first, and they come from the pin flag
        # rather than from keyword guessing. Everything below this point is a
        # heuristic read of recent activity; this part is what the user
        # explicitly said never to forget, so it is the one section that must
        # not depend on a phrase matching a keyword list.
        rules = signals["rules"]
        notes = signals["pinned_notes"]

        if rules:
            lines.append("Rules (learned from mistakes — do not repeat these):")
            for r in sorted(rules, key=lambda m: m.importance, reverse=True)[:10]:
                lines.append(f"  ! {r.content.strip()}")
            lines.append("")

        if notes:
            lines.append("Always Remember (pinned):")
            for n in notes[:10]:
                snippet = n.content.strip().replace("\n", " ")[:160]
                lines.append(f"  - {snippet}")
            lines.append("")

        if sessions:
            lines.append(f"Recent Sessions ({len(sessions)}):")
            for s in sessions:
                status = "*" if s.status == "active" else "o"
                mem_count = s.memory_count
                project_info = s.metadata.get("project", "")
                proj_str = f" [{project_info}]" if project_info else ""
                lines.append(f"  {status} {s.name}{proj_str} - {mem_count} memories - {s.id[:8]}")
                if s.metadata.get("task"):
                    lines.append(f"      Task: {s.metadata['task']}")
            lines.append("")

        # Active files from commit memories
        if commit_memories:
            lines.append("Recent Changes (from commits):")
            seen_files = set()
            for m in commit_memories[:10]:
                # Extract file paths from commit messages
                import re
                files = re.findall(r'(\w+/\w+\.\w+|\w+\.\w+)', m.content)
                for f in files:
                    if f not in seen_files and len(seen_files) < 15:
                        seen_files.add(f)
                        lines.append(f"  - {f}")
            lines.append("")

        # Decisions from recent memories. Explicit `levh-decision` tags are
        # authoritative (they survive regardless of wording); the keyword list
        # below remains only as a fallback for older, untagged memories.
        decisions = signals["decisions"]
        if decisions:
            lines.append("Recent Decisions:")
            for d in decisions[:5]:
                snippet = d.content[:120].replace("\n", " ")
                lines.append(f"  - {snippet}...")
            lines.append("")

        # Blockers / errors / TODOs. Explicit `levh-blocker` tags are
        # authoritative; keywords remain a fallback for older memories.
        blockers = signals["blockers"]
        if blockers:
            lines.append("Blockers / Errors / TODOs:")
            for b in blockers[:5]:
                snippet = b.content[:120].replace("\n", " ")
                lines.append(f"  ! {snippet}...")
            lines.append("")

        # Next suggested actions based on task
        if task:
            lines.append("Suggested Next Actions:")
            # Simple heuristic: if task mentions a file, suggest working on it
            if "test" in task.lower():
                lines.append("  1. Run failing tests")
                lines.append("  2. Fix test failures")
            elif "refactor" in task.lower():
                lines.append("  1. Continue refactoring")
                lines.append("  2. Run tests to verify")
            elif "bug" in task.lower() or "fix" in task.lower():
                lines.append("  1. Reproduce the bug")
                lines.append("  2. Implement fix")
            else:
                lines.append("  1. Continue from last session")
                lines.append("  2. Check recent decisions above")
            lines.append("")

        if len(lines) == header_only:
            return ""

        lines.append("=============================")
        return "\n".join(lines)
