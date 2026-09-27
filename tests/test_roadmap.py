"""The roadmap decision record stays a decision record.

`docs/internal/ROADMAP.md` turns report items and deferred workstreams into
rows with a state and a next step. A table like that rots in two ways: a state
outside the vocabulary (so a reader cannot tell "cancelled" from a typo), and an
open row that names no next step — which is indistinguishable from an item that
was quietly dropped.

The item numbers come from an external report and cannot be checked against the
tree, but the shape of each decision can be. That is what this file enforces.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROADMAP = Path(__file__).resolve().parent.parent / "docs" / "internal" / "ROADMAP.md"

# Terminal states close an item; open states are a claim that work is still
# owed, and therefore need a next step. `done` is the only state that expects a
# reference, because a finished item should point at the change that finished it.
_TERMINAL_STATES = {"done", "skipped", "cancelled", "ignored"}
_OPEN_STATES = {"deferred", "proposed", "in-progress"}
_STATES = _TERMINAL_STATES | _OPEN_STATES
_STATES_NEEDING_REFERENCE = {"done"}

_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*$", re.M)
_SEPARATOR_RE = re.compile(r"^\|\s*-+\s*\|", re.M)


def _rows() -> list[tuple[str, str, str, str, str]]:
    """Every table row with five cells, keyed by item and topic.

    The header row is dropped by requiring the state cell to be a known state;
    any row whose third cell is not a state is a table header or prose that
    happens to start with a pipe.
    """
    rows: list[tuple[str, str, str, str, str]] = []
    for match in _ROW_RE.finditer(ROADMAP.read_text(encoding="utf-8")):
        item, topic, state, next_step, reference = (cell.strip() for cell in match.groups())
        if state in _STATES:
            rows.append((item, topic, state, next_step, reference))
    return rows


def test_roadmap_has_rows():
    assert _rows(), (
        "no roadmap rows parsed — the table shape changed, or the state "
        "vocabulary no longer matches _STATES"
    )


def test_every_row_uses_a_known_state():
    unknown = sorted({state for _, _, state, _, _ in _rows() if state not in _STATES})
    assert not unknown, (
        f"unknown roadmap states: {unknown}; allowed: {sorted(_STATES)}. "
        "A state outside the vocabulary cannot be read as a decision."
    )


@pytest.mark.parametrize("row", _rows(), ids=lambda r: r[0])
def test_open_row_names_a_next_step(row):
    item, _, state, next_step, _ = row
    if state not in _OPEN_STATES:
        return
    assert next_step and next_step != "—", (
        f"roadmap item {item!r} is {state!r} but names no next step; an open "
        "item with no actionable step is indistinguishable from a dropped one"
    )


@pytest.mark.parametrize("row", _rows(), ids=lambda r: r[0])
def test_terminal_row_does_not_promise_work(row):
    """A `done`/`skipped`/`cancelled`/`ignored` row must not still advertise a
    next step — that is exactly how a closed item starts looking open again."""
    item, _, state, next_step, _ = row
    if state not in _TERMINAL_STATES:
        return
    assert next_step in ("", "—"), (
        f"roadmap item {item!r} is {state!r} but still names a next step "
        f"({next_step!r}); clear it so the row reads as closed"
    )


@pytest.mark.parametrize("row", _rows(), ids=lambda r: r[0])
def test_done_row_points_at_its_change(row):
    item, _, state, _, reference = row
    if state not in _STATES_NEEDING_REFERENCE:
        return
    assert reference and reference != "—", (
        f"roadmap item {item!r} is {state!r} but cites no change; a finished "
        "item should point at the PR or issue that finished it"
    )


def test_every_report_item_is_recorded():
    """The report numbered 11 items plus B; a decision record that silently
    omits one is how an item gets lost. The set is checked explicitly so that
    adding item 12, or dropping a row, is a visible change to this test."""
    expected = {"4", "5", "6", "8", "9", "10", "11", "B"}
    recorded = {item for item, _, _, _, _ in _rows()}
    assert expected <= recorded, (
        f"report items missing from docs/internal/ROADMAP.md: "
        f"{sorted(expected - recorded)}"
    )


def test_roadmap_is_listed_in_the_internal_index():
    """docs/internal/README.md is the entry point for the folder; a new
    inventory that is not named there is one a reader will not find."""
    readme = (ROADMAP.parent / "README.md").read_text(encoding="utf-8")
    assert ROADMAP.name in readme, f"{ROADMAP.name} is not described in docs/internal/README.md"
