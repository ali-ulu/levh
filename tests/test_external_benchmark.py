"""The external benchmark adapter: shape, determinism, privacy (#340).

The adapter's value is that its numbers are reproducible and its report leaks
nothing. These tests pin those two properties with a synthetic LoCoMo-shaped
conversation — the real release is not vendored (it is a research dataset), so
the protocol is tested on data built here, not on a downloaded file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.core.external_benchmark import (
    ADAPTER_VERSION,
    ADVERSARIAL_CATEGORY,
    load_locomo,
    run_locomo,
    turns_of,
)

ARTIFACT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "external_benchmark"
    / "locomo10_retrieval_hash.json"
)

# Distinct wording, no near-duplicates: the admission gate rejects exact
# duplicates, and an accidental one would shrink `stored` and confuse the run.
_CONVERSATION = {
    "speaker_a": "Ann",
    "speaker_b": "Bob",
    "session_1_date_time": "1:00 pm on 5 May, 2023",
    "session_1": [
        {
            "speaker": "Ann",
            "dia_id": "D1:1",
            "text": "The launch date for the Zephyr project is March fourteenth",
        },
        {
            "speaker": "Bob",
            "dia_id": "D1:2",
            "text": "The office coffee machine is on the third floor",
        },
    ],
    "session_2_date_time": "2:00 pm on 6 May, 2023",
    "session_2": [
        {
            "speaker": "Ann",
            "dia_id": "D2:1",
            "text": "The backup retention window is ninety days",
        },
    ],
}

_QA = [
    {
        "question": "When is the launch date for the Zephyr project",
        "answer": "March fourteenth",
        "evidence": ["D1:1"],
        "category": 1,
    },
    {
        "question": "How long is the backup retention window",
        "answer": "ninety days",
        "evidence": ["D2:1"],
        "category": 4,
    },
    {
        "question": "What colour is the sky today",
        "adversarial_answer": "blue",
        "evidence": ["D1:2"],
        "category": ADVERSARIAL_CATEGORY,
    },
]


def _write_dataset(tmp_path: Path) -> Path:
    sample = {"sample_id": "conv-test", "conversation": _CONVERSATION, "qa": _QA}
    path = tmp_path / "locomo10.json"
    path.write_text(json.dumps([sample]), encoding="utf-8")
    return path


def test_turns_of_orders_sessions_numerically():
    """session_10 must not sort before session_2 — a lexical sort gets this
    wrong and would feed the conversation out of order."""
    conversation = {
        "session_2_date_time": "day two",
        "session_2": [{"speaker": "A", "dia_id": "D2:1", "text": "second"}],
        "session_10_date_time": "day ten",
        "session_10": [{"speaker": "A", "dia_id": "D10:1", "text": "tenth"}],
    }
    turns = turns_of(conversation)
    assert [t["dia_id"] for t in turns] == ["D2:1", "D10:1"]
    assert turns[1]["date"] == "day ten"


def test_turns_of_flattens_all_sessions_in_order():
    turns = turns_of(_CONVERSATION)
    assert [t["dia_id"] for t in turns] == ["D1:1", "D1:2", "D2:1"]
    assert turns[0]["speaker"] == "Ann"


def test_load_locomo_missing_file_names_the_fix(tmp_path):
    with pytest.raises(FileNotFoundError, match="locomo10.json"):
        load_locomo(tmp_path / "absent.json")


def test_load_locomo_rejects_a_non_locomo_json(tmp_path):
    path = tmp_path / "other.json"
    path.write_text(json.dumps({"not": "locomo"}), encoding="utf-8")
    with pytest.raises(ValueError, match="not the LoCoMo release"):
        load_locomo(path)


@pytest.mark.asyncio
async def test_run_locomo_reports_retrieval_and_adversarial_separately(tmp_path):
    report = await run_locomo(_write_dataset(tmp_path), embedder_mode="hash", top_k=10)

    assert report["benchmark"] == "locomo10"
    assert report["adapter_version"] == ADAPTER_VERSION
    assert report["samples"] == 1
    assert report["samples_total"] == 1

    # Categories 1 and 4 carry an evidence label; category 5 does not count
    # toward the retrieval metric.
    assert report["retrieval"]["questions"] == 2
    assert set(report["retrieval_by_category"]) == {"1", "4"}
    assert report["adversarial"]["questions"] == 1
    assert report["adversarial"]["metric_kind"] == "evidence_retrieval_proxy"


@pytest.mark.asyncio
async def test_evidence_that_matches_the_question_is_retrieved(tmp_path):
    report = await run_locomo(_write_dataset(tmp_path), embedder_mode="hash", top_k=10)
    assert report["retrieval"]["hit_at_1"] == 1.0
    assert report["retrieval"]["mrr"] == 1.0


@pytest.mark.asyncio
async def test_run_is_deterministic(tmp_path):
    """Two runs of the same dataset must be byte-identical — that is the
    property the committed artifact relies on."""
    path = _write_dataset(tmp_path)
    first = await run_locomo(path, embedder_mode="hash", top_k=10)
    second = await run_locomo(path, embedder_mode="hash", top_k=10)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


@pytest.mark.asyncio
async def test_report_never_contains_dataset_content(tmp_path):
    """The report is counts and rates only. A leaked question or turn would
    make the artifact a privacy problem, not just a big file."""
    report = await run_locomo(_write_dataset(tmp_path), embedder_mode="hash", top_k=10)
    blob = json.dumps(report)
    for text in (
        "Zephyr",
        "March fourteenth",
        "coffee machine",
        "backup retention",
        "colour is the sky",
        "Ann",
        "Bob",
    ):
        assert text not in blob, f"report leaked dataset text: {text!r}"


def test_committed_artifact_is_well_formed_and_content_free():
    """The artifact shipped with the repo must carry the current adapter
    version and the fields a reader needs, and nothing else."""
    report = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert report["adapter_version"] == ADAPTER_VERSION
    assert report["benchmark"] == "locomo10"
    assert report["samples"] == report["samples_total"] == 10
    assert report["retrieval"]["questions"] > 0
    assert report["adversarial"]["metric_kind"] == "evidence_retrieval_proxy"
    assert report["protocol"]["judge"] == "none — retrieval-side only"
    # No dataset text: the artifact is 2.3 KB of counts, not a transcript.
    assert ARTIFACT.stat().st_size < 10_000
