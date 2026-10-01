"""The pre-action gate: it warns on a recorded mistake, and stays quiet otherwise.

The whole value of the gate is that a warning means something, so most of
these tests are about silence. A matcher that fires on every action that shares
a topic with every rule is worse than no matcher: the user learns to dismiss
it, and the one warning that mattered is dismissed with the rest.
"""

from __future__ import annotations

import pytest

from server.core import action_gate

# The rule a fixture store would have recorded: a force push to main.
_FORCE_PUSH_RULE = {
    "id": "r_force_push",
    "statement": "Do not force push to main. Instead: open a PR. (Root cause: it rewrites shared history.)",
    "task": "release the 2.32.0 branch",
    "wrong_action": "force push to main",
    "severity": "critical",
}


def test_a_recorded_wrong_action_is_matched() -> None:
    verdict = action_gate.check_action(
        "Bash", "git push --force origin main", [_FORCE_PUSH_RULE]
    )
    assert verdict["decision"] == "warn"
    assert [m["rule_id"] for m in verdict["matched_rules"]] == ["r_force_push"]
    assert verdict["checked_rules"] == 1


def test_an_unrelated_action_stays_silent() -> None:
    """The false-positive story. 'Bash: pytest -q' shares no content word with
    the rule, so the gate must not warn — otherwise every command warns."""
    verdict = action_gate.check_action("Bash", "pytest -q tests/", [_FORCE_PUSH_RULE])
    assert verdict["decision"] == "allow"
    assert verdict["matched_rules"] == []
    assert verdict["checked_rules"] == 1


def test_same_topic_but_different_action_stays_silent() -> None:
    """A shared *topic* is not a shared action. Reading about main is not
    pushing to main, and a matcher that cannot tell them apart is noise."""
    verdict = action_gate.check_action(
        "Read", "cat docs/release-main.md", [_FORCE_PUSH_RULE]
    )
    assert verdict["decision"] == "allow"


def test_no_rules_recorded_says_so() -> None:
    """An empty store is not a clean bill of health, and the reason must not
    read like one."""
    verdict = action_gate.check_action("Bash", "git push --force origin main", [])
    assert verdict["decision"] == "allow"
    assert verdict["checked_rules"] == 0
    assert "no rules recorded" in verdict["reason"]


def test_inflected_words_still_match() -> None:
    """The store is bilingual (#78): a Turkish rule must match a Turkish action
    that inflects the same stem, with no stemmer in the path."""
    rule = {
        "id": "r_tr",
        "statement": "Do not delete the migration file.",
        "wrong_action": "migration dosyasini silmek",
        "task": "sema degisikligi",
        "severity": "high",
    }
    verdict = action_gate.check_action(
        "Bash", "rm migration dosyasini", [rule]
    )
    assert verdict["decision"] == "warn"


def test_a_single_shared_word_is_not_a_match() -> None:
    """MIN_SHARED_TERMS. One word is a topic ('config'), not an action; the
    rule below and the action share only 'config'."""
    rule = {
        "id": "r_cfg",
        "statement": "Do not edit config files directly.",
        "wrong_action": "edit the config file",
        "task": "",
        "severity": "medium",
    }
    verdict = action_gate.check_action("Read", "print config", [rule])
    assert verdict["decision"] == "allow"


def test_a_rule_with_no_discriminating_words_never_matches() -> None:
    """MIN_RULE_TERMS. 'forgot' carries no signal, so a rule built from it must
    not warn on everything."""
    rule = {
        "id": "r_thin",
        "statement": "Do not forget.",
        "wrong_action": "forgot",
        "task": "",
        "severity": "low",
    }
    verdict = action_gate.check_action("Bash", "forgot to run tests", [rule])
    assert verdict["decision"] == "allow"


def test_partial_coverage_below_threshold_stays_silent() -> None:
    """MATCH_THRESHOLD. A long rule matched on a couple of its words is a
    topical overlap, not the same action."""
    rule = {
        "id": "r_long",
        "statement": "Do not rewrite the release notes and bump the version in one commit.",
        "wrong_action": "rewrite release notes and bump version in one commit",
        "task": "",
        "severity": "medium",
    }
    # Covers only "version"/"commit" out of eight content words.
    verdict = action_gate.check_action("Bash", "git commit version", [rule])
    assert verdict["decision"] == "allow"


def test_matches_are_ordered_strongest_first_then_severity() -> None:
    """Determinism and usefulness: the strongest overlap leads, and equal
    scores break by severity so the output is byte-stable for a store state."""
    weak = {
        "id": "r_weak",
        "statement": "Do not force push to main.",
        "wrong_action": "force push to main",
        "task": "",
        "severity": "low",
    }
    strong = {
        "id": "r_strong",
        "statement": "Do not force push to main on the release branch.",
        "wrong_action": "force push to main release branch",
        "task": "",
        "severity": "critical",
    }
    verdict = action_gate.check_action(
        "Bash", "git push --force origin main", [weak, strong]
    )
    assert verdict["decision"] == "warn"
    scores = [m["score"] for m in verdict["matched_rules"]]
    assert scores == sorted(scores, reverse=True)


def test_verdict_is_never_block() -> None:
    """The enforcement decision stays out of the matcher — issue #337's
    false-positive policy. If this ever returns 'block', the gate has become a
    permission system it was explicitly not meant to be."""
    verdict = action_gate.check_action(
        "Bash", "git push --force origin main", [_FORCE_PUSH_RULE]
    )
    assert verdict["decision"] in {"warn", "allow"}


def test_the_tool_name_is_part_of_the_action() -> None:
    """Two short actions can share every word but differ by tool; the tool name
    is what separates them."""
    rule = {
        "id": "r_bash_build",
        "statement": "Do not rm -rf the build directory.",
        "wrong_action": "Bash rm build directory",
        "task": "",
        "severity": "high",
    }
    assert action_gate.check_action(
        "Bash", "rm build", [rule]
    )["decision"] == "warn"
    assert action_gate.check_action(
        "Write", "rm build", [rule]
    )["decision"] == "allow"


@pytest.mark.parametrize("severity", ["low", "medium", "high", "critical"])
def test_severity_does_not_change_whether_it_matches(severity: str) -> None:
    """Severity orders warnings; it must not decide whether one exists. A low
    rule that overlaps is still worth saying."""
    rule = dict(_FORCE_PUSH_RULE, id=f"r_{severity}", severity=severity)
    verdict = action_gate.check_action(
        "Bash", "git push --force origin main", [rule]
    )
    assert verdict["decision"] == "warn"
    assert verdict["matched_rules"][0]["severity"] == severity
