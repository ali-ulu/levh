"""Tools 64 & 65: record_mistake / list_mistakes — Mistake guard, record side."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.guard import SEVERITIES, GuardService
from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    guard = GuardService(engine.db, engine)

    @mcp.tool()
    async def check_action(
        action_text: str,
        tool_name: str = "",
        project: str = "",
    ) -> str:
        """Check a proposed action against the rules learned from mistakes.

        Call this BEFORE a risky tool call — a force push, a destructive
        command, an edit to a file that has bitten you before. It matches the
        action's own words against the ``wrong_action`` and task of every rule
        the guard recorded, with no model and no network.

        The verdict is ADVISORY: it returns "allow" or "warn". It never blocks
        and it never decides for you — a warning means "this overlaps something
        you got wrong before", which you then judge in context. An empty inbox
        means nothing matched, not that the action is safe.

        Args:
            action_text: What the action would do — the command, the edit, the call.
            tool_name: Tool it would run through ("Bash", "Write"), when known.
            project: Scope to one project. Empty = rules from every project.
        """
        verdict = await guard.check_action(
            tool_name=tool_name,
            action_text=action_text,
            project=project or None,
        )

        if verdict["decision"] == "allow":
            return (
                f"allow — {verdict['reason']} "
                f"({verdict['checked_rules']} rule(s) checked)."
            )

        lines = [
            f"warn — {verdict['reason']}.",
            "",
        ]
        for match in verdict["matched_rules"]:
            lines.append(
                f"- [{match['severity']}] {match['statement']}"
                f"\n    match {match['score']:.2f} on: {', '.join(match['matched_terms'])}"
            )
        lines.append("")
        lines.append(
            "This is a signal from your own history, not a permission check — "
            "review the rule above and decide whether it applies here."
        )
        return "\n".join(lines)

    @mcp.tool()
    async def record_mistake(
        task: str,
        wrong_action: str,
        correct_action: str,
        root_cause: str = "",
        tool_name: str = "",
        severity: str = "medium",
        source: str = "user",
        project: str = "",
    ) -> str:
        """Record a mistake so it becomes a rule that survives this session.

        The rule is stored as a pinned memory — pinned memories never decay —
        and shows up in generated context files (CLAUDE.md / .cursorrules)
        under "Rules Learned From Mistakes", so a later session reads it
        before working. Call this when a mistake has been identified and
        corrected, not for every failed attempt.

        Args:
            task: What was being attempted.
            wrong_action: What was done wrong. Becomes the "Do not ..." clause.
            correct_action: What should have been done instead.
            root_cause: Why it happened — the structural reason, not "I forgot".
            tool_name: Tool involved, when a specific one was ("Bash", "Write").
            severity: low | medium | high | critical. Raises the rule's importance.
            source: Who identified it — "user", "test", "review".
            project: Scope the rule to one project. Empty = all projects.
        """
        try:
            result = await guard.record_mistake(
                task=task,
                wrong_action=wrong_action,
                correct_action=correct_action,
                root_cause=root_cause,
                tool_name=tool_name,
                severity=severity,
                source=source,
                project=project or None,
            )
        except ValueError as exc:
            return f"Could not record the mistake: {exc}"

        return (
            f"Mistake recorded as a pinned rule (severity: {result['severity']}).\n"
            f"  Rule ID: {result['rule_id']}\n"
            f"  Rule: {result['statement']}\n"
            f"  Violations on record: {result['total_violations']}\n"
            f"This rule will not decay and will appear in generated context files."
        )

    @mcp.tool()
    async def list_mistakes(
        days: int = 0,
        severity: str = "",
        limit: int = 20,
    ) -> str:
        """List recorded mistakes, newest first.

        Args:
            days: Only mistakes from the last N days. 0 = all time.
            severity: Filter by low | medium | high | critical. Empty = all.
            limit: Maximum entries to return.
        """
        if severity and severity.strip().lower() not in SEVERITIES:
            return f"Unknown severity '{severity}'. Expected one of: {', '.join(SEVERITIES)}."

        rows = await guard.list_violations(
            days=days or None,
            severity=severity or None,
            limit=limit,
        )
        if not rows:
            return "No mistakes recorded."

        lines = [f"{len(rows)} mistake(s) on record:"]
        for row in rows:
            when = (row["occurred_at"] or "")[:10]
            lines.append(
                f"- [{row['severity']}] {when} — {row['wrong_action']}"
                + (f"  (task: {row['task']})" if row.get("task") else "")
                + f"\n    rule: {row['rule_id']}"
            )
        return "\n".join(lines)
