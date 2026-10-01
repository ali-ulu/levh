"""Pre-action judgment gate — does a *proposed* action repeat a recorded mistake?

:mod:`server.core.guard` stops at recording: a mistake becomes a pinned rule
plus a violation row, and reading them back is as far as it goes. This module
crosses the line that docstring drew, and it crosses it the only way the
docstring permits — as a **deterministic, model-free matcher** with an explicit
false-positive story.

Why a separate module rather than a method on ``GuardService``: the decision
is a pure function of the action text and the recorded rules, and keeping it
pure is what makes the hot path cheap and the behaviour testable without a
database. ``GuardService.check_action`` supplies the rules and this supplies
the verdict.

The signal is lexical, the same shape as :mod:`server.core.lexical` and
:mod:`server.core.conflict`: no model, no network, no dependency. A proposed
action matches a rule when the rule's own recorded ``wrong_action`` (and the
task it was about) overlaps it on content words. That is deliberately narrow —
it fires on the same *action*, not on the same topic — because the cost of a
false warning is that a user learns to ignore the gate.

The verdict is **advisory**. ``block`` is never returned here: this module
reports what it matched and how sure it is, and the caller decides what to do
with a warning. Keeping the enforcement decision out of the matcher is what
lets the gate stay on the hot path without a permission system behind it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .lexical import _stem_matches, terms

# A proposed action matches a rule when it covers at least this share of the
# rule's content words. Kept above ``lexical``'s recall-oriented 0.0 floor:
# recall can afford a false positive because the user sees a ranked list and
# ignores the tail, whereas the gate interrupts before an action runs, so a
# spurious warning is paid every time.
MATCH_THRESHOLD = 0.6

# Two content words is the shortest match that carries information. "deploy
# prod" against "deploy prod" is a real signal; a single shared word ("config")
# is a topic, not an action, and warning on it would fire constantly.
MIN_SHARED_TERMS = 2

# Below this many content words a rule is too thin to match on: a wrong_action
# of "forgot" has no discriminating power.
MIN_RULE_TERMS = 2


def _overlap(rule_terms: set[str], action_terms: set[str]) -> list[str]:
    """The rule's content words that the action covers, sorted for stability.

    Coverage, not Jaccard, mirroring :mod:`server.core.lexical`: the question
    is "did the proposed action do the thing the rule forbids", so a short
    action that contains every rule word is a full match even though it shares
    no words the other way. Inflections are absorbed by ``_stem_matches``
    (``deploy``/``deploying``, ``migrasyonu``/``migrasyonlar``).
    """
    return sorted(
        term
        for term in rule_terms
        if any(_stem_matches(term, word) for word in action_terms)
    )


def action_terms(tool_name: str, action_text: str) -> set[str]:
    """Content words a proposed action is judged on.

    The tool name is part of the action, not context around it: "Bash: rm -rf
    build" and "Write: build notes" share the word "build" but are different
    actions, and only the tool name separates them when the text is short.
    """
    return terms(f"{tool_name or ''} {action_text or ''}")


def match_rule(
    tool_name: str,
    action_text: str,
    rule_wrong_action: str,
    rule_task: str = "",
    threshold: float = MATCH_THRESHOLD,
) -> dict | None:
    """Whether one proposed action matches one rule. Pure; returns the evidence
    or ``None``.

    A rule carries two descriptions of the same mistake — its
    ``wrong_action`` (the sentence the guard composed into "Do not ...") and
    the ``task`` it happened in — and they are scored **separately**, taking
    the stronger. Pooling their words into one denominator would let a verbose
    task dilute a terse ``wrong_action`` until nothing matched: "migration
    dosyasini silmek" scores 2/3 against a matching action, but 2/5 once a
    three-word task is added to the same bag. Scoring each on its own means a
    short, sharp rule stays sharp, and a rule whose ``wrong_action`` is vague
    can still be caught by the task it was recorded under.
    """
    proposed = action_terms(tool_name, action_text)
    best: dict | None = None

    for description in (rule_wrong_action, rule_task):
        description_terms = terms(description)
        if len(description_terms) < MIN_RULE_TERMS:
            continue
        covered = _overlap(description_terms, proposed)
        if len(covered) < MIN_SHARED_TERMS:
            continue
        score = len(covered) / len(description_terms)
        if score < threshold:
            continue
        if best is None or score > best["score"]:
            best = {"score": round(score, 4), "matched_terms": covered}

    return best


def check_action(
    tool_name: str,
    action_text: str,
    rules: Iterable[Mapping],
    threshold: float = MATCH_THRESHOLD,
) -> dict:
    """Judge a proposed action against recorded rules. Pure and deterministic.

    ``rules`` is an iterable of mappings carrying the fields the guard writes
    into a rule memory's metadata: ``id``, ``wrong_action``, ``task``,
    ``severity``, ``statement``. Unknown extra fields are ignored so a caller
    can pass the raw metadata dict.

    Returns ``{decision, matched_rules, reason, checked_rules}``. ``decision``
    is ``warn`` when anything matched and ``allow`` otherwise — never
    ``block``, because whether a warning is an instruction is the caller's
    policy, not this function's (see the module docstring).

    Matches are returned strongest first, ties broken by severity then id, so
    the output is byte-stable for a given store state — the same determinism
    contract the evaluation harness holds.
    """
    proposed = action_terms(tool_name, action_text)
    matched: list[dict] = []

    checked = 0
    for rule in rules:
        checked += 1
        verdict = match_rule(
            tool_name,
            action_text,
            str(rule.get("wrong_action") or ""),
            str(rule.get("task") or ""),
            threshold=threshold,
        )
        if verdict is None:
            continue
        matched.append(
            {
                "rule_id": rule.get("id"),
                "statement": rule.get("statement") or "",
                "severity": rule.get("severity") or "medium",
                "score": verdict["score"],
                "matched_terms": verdict["matched_terms"],
            }
        )

    if not matched:
        return {
            "decision": "allow",
            "matched_rules": [],
            "reason": (
                "no recorded rule overlaps this action"
                if checked
                else "no rules recorded yet"
            ),
            "checked_rules": checked,
        }

    severity_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    matched.sort(
        key=lambda m: (
            -m["score"],
            -severity_rank.get(str(m["severity"]).lower(), 1),
            str(m["rule_id"]),
        )
    )

    top = matched[0]
    count = len(matched)
    return {
        "decision": "warn",
        "matched_rules": matched,
        "reason": (
            f"{count} recorded rule{'s' if count != 1 else ''} overlap this action; "
            f"strongest is {top['score']:.2f} on {', '.join(top['matched_terms'])}"
        ),
        "checked_rules": checked,
    }
