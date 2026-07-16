"""Memory evaluation harness (2.25) — golden-fixture measurement of what the
memory system actually does, entirely offline.

Runs a set of golden fixtures (``tests/fixtures/evaluation/*.json``) through
the *real* pipeline — admission gate → store → trust recompute → conflict
detection → recall → review — and aggregates the outcomes into a single
report. No LLM, no network, no mocks: every number in the report comes from
executing the same code paths a live install runs.

Determinism contract: with the hash embedder and a fixed fixture set, two
consecutive runs must produce byte-identical reports. Nothing time- or
random-dependent may leak into the report (no timestamps, no uuids — fixture
*keys* are used in place of generated memory ids).

Privacy contract: the report contains fixture keys, scenario names, labels
and numbers only — never the raw memory content that was stored. A test
asserts this by scanning the serialized report for fixture content strings.

The report is a *measurement*, not a verdict: conflict candidates stay review
signals, trust stays advisory, and nothing here alters H-score ranking.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from server.core.memory_engine import MemoryEngine

EVALUATION_VERSION = "memory-eval-v1"

# Golden fixtures ship inside the Python package so `stackmemory eval run`
# behaves the same from a wheel and a source checkout. `--fixtures` can still
# point at a custom external corpus.
DEFAULT_FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent / "fixtures" / "evaluation"
)


def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version("stackmemory")
    except Exception:
        return "unknown"


def load_fixtures(fixture_dir: str | os.PathLike | None = None) -> list[dict]:
    """Load every ``*.json`` golden fixture, sorted by filename so run order
    (and therefore the report) is deterministic."""
    directory = Path(fixture_dir) if fixture_dir else DEFAULT_FIXTURE_DIR
    if not directory.is_dir():
        raise FileNotFoundError(f"fixture directory not found: {directory}")
    fixtures = []
    for path in sorted(directory.glob("*.json")):
        with open(path, encoding="utf-8") as fh:
            fx = json.load(fh)
        fx.setdefault("name", path.stem)
        fixtures.append(fx)
    if not fixtures:
        raise FileNotFoundError(f"no fixture .json files in {directory}")
    return fixtures


def _expected_labels(value) -> list[str]:
    """A fixture may pin one trust label or accept several ("medium" or
    "medium_high" are one threshold apart with the hash embedder)."""
    return [value] if isinstance(value, str) else list(value)


async def _run_fixture(fixture: dict, embedder_mode: str) -> dict:
    """Execute one golden fixture on a fresh throwaway engine and return its
    raw per-fixture outcome (keys/labels/ranks only — no content)."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    engine = MemoryEngine(db_path=db_path, embedder_mode=embedder_mode)
    await engine.initialize()
    try:
        outcome: dict = {
            "name": fixture["name"],
            "admission": [],  # [{key, action, expected, ok}]
            "queries": [],  # [{rank, expected_hit, forbidden_violation}]
            "conflicts": {},
            "trust": [],  # [{key, label, expected, ok}]
            "review": [],  # [{key, action}]
            "recovered": [],  # post-review query hits
        }
        key_to_id: dict[str, str] = {}

        # 1) Admission → store. Every item flows through the real gate.
        for item in fixture.get("memories", []):
            result = await engine.admit_memory(
                content=item["content"],
                importance=item.get("importance", 0.5),
                tags=item.get("tags"),
                project=item.get("project"),
                source=item.get("source"),
                pinned=item.get("pinned", False),
                memory_type=item.get("memory_type", "episodic"),
                metadata=item.get("metadata"),
                force=item.get("force", False),
            )
            action = result["decision"]["action"]
            expected = item.get("expected_admission")
            outcome["admission"].append(
                {
                    "key": item["key"],
                    "action": action,
                    "reason_codes": result["decision"].get("reason_codes", []),
                    "expected": expected,
                    "ok": expected is None or action == expected,
                }
            )
            if result["stored"]:
                key_to_id[item["key"]] = result["memory"]["id"]

        # 1b) Pre-evaluation lifecycle: a "confirmed human memory" is one the
        #     user has actually reinforced, not just stored — model that
        #     before trust is computed.
        for key in fixture.get("reinforce_before_eval", []):
            if key in key_to_id:
                await engine.reinforce_memory(key_to_id[key])

        # 2) Derived pipeline: trust then conflict candidates (same order the
        #    live pipeline uses; detection reads trust context).
        await engine.recompute_trust_scores()
        await engine.detect_conflict_candidates()

        # 3) Recall queries.
        for q in fixture.get("queries", []):
            res = await engine.recall(query=q["query"], top_k=10, reinforce=False)
            ranked_ids = [m.id for m in res.memories]
            expected_ids = [
                key_to_id[k] for k in q.get("expected_memory_keys", []) if k in key_to_id
            ]
            forbidden_ids = [
                key_to_id[k] for k in q.get("forbidden_memory_keys", []) if k in key_to_id
            ]
            best_rank = None
            for eid in expected_ids:
                if eid in ranked_ids:
                    r = ranked_ids.index(eid) + 1
                    best_rank = r if best_rank is None else min(best_rank, r)
            # A forbidden memory violates the query if it outranks every
            # expected memory (i.e. the superseded fact would win).
            violation = False
            for fid in forbidden_ids:
                if fid in ranked_ids:
                    fr = ranked_ids.index(fid) + 1
                    if best_rank is None or fr < best_rank:
                        violation = True
            outcome["queries"].append(
                {"rank": best_rank, "forbidden_violation": violation}
            )

        # 4) Conflict candidates vs expectations.
        id_to_key = {v: k for k, v in key_to_id.items()}
        detected = await engine.list_conflict_candidates(status="open", limit=100)
        detected_pairs = {
            frozenset(
                (
                    id_to_key.get(c["memory_id_a"], c["memory_id_a"]),
                    id_to_key.get(c["memory_id_b"], c["memory_id_b"]),
                )
            )
            for c in detected
        }
        expected_pairs = {
            frozenset(pair) for pair in fixture.get("expected_conflicts", [])
        }
        tp = len(detected_pairs & expected_pairs)
        outcome["conflicts"] = {
            "detected": len(detected_pairs),
            "expected": len(expected_pairs),
            "true_positives": tp,
            "false_positives": len(detected_pairs - expected_pairs),
            "missed": len(expected_pairs - detected_pairs),
        }
        if "expected_conflict_count" in fixture:
            outcome["conflicts"]["count_ok"] = (
                len(detected_pairs) == fixture["expected_conflict_count"]
            )

        # 5) Trust labels.
        for key, expected in fixture.get("expected_trust_labels", {}).items():
            label = None
            if key in key_to_id:
                bd = await engine.get_trust(key_to_id[key])
                label = bd["label"] if bd else None
            accepted = _expected_labels(expected)
            outcome["trust"].append(
                {"key": key, "label": label, "expected": accepted, "ok": label in accepted}
            )

        # 6) Lifecycle: apply declared review actions through the real path.
        for rv in fixture.get("review_actions", []):
            if rv["key"] in key_to_id:
                await engine.apply_review(key_to_id[rv["key"]], rv["action"])
                outcome["review"].append({"key": rv["key"], "action": rv["action"]})

        # 7) Post-review recovery queries (fading-memory recovery).
        for q in fixture.get("post_review_queries", []):
            res = await engine.recall(query=q["query"], top_k=10, reinforce=False)
            ranked_ids = [m.id for m in res.memories]
            hit = any(
                key_to_id.get(k) in ranked_ids
                for k in q.get("expected_memory_keys", [])
            )
            outcome["recovered"].append(hit)

        return outcome
    finally:
        await engine.shutdown()
        try:
            os.unlink(db_path)
        except OSError:
            pass


async def run_evaluation(
    fixture_dir: str | os.PathLike | None = None, embedder_mode: str = "hash"
) -> dict:
    """Run the full golden-fixture evaluation and return the aggregate report.

    Deterministic for a given fixture set + embedder mode. The report never
    contains raw memory content.
    """
    fixtures = load_fixtures(fixture_dir)
    outcomes = [await _run_fixture(fx, embedder_mode) for fx in fixtures]

    # ── Recall ────────────────────────────────────────────────────
    ranks = [q["rank"] for o in outcomes for q in o["queries"]]
    total_q = len(ranks)
    hit1 = sum(1 for r in ranks if r == 1)
    hit3 = sum(1 for r in ranks if r is not None and r <= 3)
    mrr = sum(1.0 / r for r in ranks if r is not None)
    forbidden_violations = sum(
        1 for o in outcomes for q in o["queries"] if q["forbidden_violation"]
    )

    # ── Memory quality (admission surface) ────────────────────────
    admissions = [a for o in outcomes for a in o["admission"]]
    total_a = len(admissions)
    by_action: dict[str, int] = {}
    for a in admissions:
        by_action[a["action"]] = by_action.get(a["action"], 0) + 1
    admission_mismatches = sum(1 for a in admissions if not a["ok"])
    accepted = by_action.get("admit", 0) + by_action.get("redact", 0)

    # ── Conflicts ─────────────────────────────────────────────────
    det = sum(o["conflicts"]["detected"] for o in outcomes)
    tp = sum(o["conflicts"]["true_positives"] for o in outcomes)
    fp = sum(o["conflicts"]["false_positives"] for o in outcomes)
    expected_c = sum(o["conflicts"]["expected"] for o in outcomes)

    # ── Lifecycle ─────────────────────────────────────────────────
    review_dist: dict[str, int] = {}
    for o in outcomes:
        for rv in o["review"]:
            review_dist[rv["action"]] = review_dist.get(rv["action"], 0) + 1
    recoveries = [h for o in outcomes for h in o["recovered"]]

    # ── Trust ─────────────────────────────────────────────────────
    trust_checks = [t for o in outcomes for t in o["trust"]]

    def _rate(n: int, d: int) -> float:
        return round(n / d, 4) if d else 0.0

    fixture_results = []
    for fx, o in zip(fixtures, outcomes):
        # A fixture may declare its detections known false positives (it
        # exists to *measure* the FP surface); those FPs still count in the
        # aggregate but don't fail the fixture.
        fp_ok = o["conflicts"]["false_positives"] == 0 or fx.get(
            "known_false_positives", False
        )
        fixture_results.append(
            {
                "name": o["name"],
                "passed": (
                    all(a["ok"] for a in o["admission"])
                    and all(t["ok"] for t in o["trust"])
                    and not any(q["forbidden_violation"] for q in o["queries"])
                    and o["conflicts"].get("count_ok", True)
                    and o["conflicts"]["missed"] == 0
                    and fp_ok
                ),
            }
        )

    return {
        "evaluation_version": EVALUATION_VERSION,
        "stackmemory_version": _package_version(),
        "embedder_mode": embedder_mode,
        "fixture_count": len(fixtures),
        "fixtures": fixture_results,
        # "recall" here measures golden-fixture retrieval (hit@k / MRR over
        # engine.recall top-10), not admission or conflict surfaces.
        "recall": {
            "queries": total_q,
            "hit_at_1": _rate(hit1, total_q),
            "hit_at_3": _rate(hit3, total_q),
            "mrr": round(mrr / total_q, 4) if total_q else 0.0,
            "forbidden_violations": forbidden_violations,
        },
        # "quality" measures the admission gate's verdict distribution over
        # fixture items, plus how often it disagreed with the fixture's
        # expected verdict.
        "quality": {
            "items": total_a,
            "admission_accept_rate": _rate(accepted, total_a),
            "review_rate": _rate(by_action.get("review", 0), total_a),
            "reject_rate": _rate(by_action.get("reject", 0), total_a),
            "redaction_rate": _rate(by_action.get("redact", 0), total_a),
            # Duplicates are counted by the gate's machine reason codes, not
            # by verdict — a reject for another reason (e.g. too_short) must
            # not inflate this.
            "duplicate_rate": _rate(
                sum(
                    1
                    for a in admissions
                    if {"duplicate_exact", "duplicate_near"} & set(a["reason_codes"])
                ),
                total_a,
            ),
            "admission_mismatches": admission_mismatches,
            # Scope is explicit in the name: only memories a fixture checks
            # via expected_trust_labels are counted, not every stored memory.
            "checked_low_trust_count": sum(
                1 for t in trust_checks if t["label"] in ("low", "very_low")
            ),
            "trust_label_mismatches": sum(1 for t in trust_checks if not t["ok"]),
        },
        # "conflicts" measures candidate detection (review signals, never
        # verdicts) against fixture-declared expected pairs.
        "conflicts": {
            "expected": expected_c,
            "detected": det,
            "precision": _rate(tp, det),
            "recall": _rate(tp, expected_c),
            "false_positives": fp,
        },
        # "lifecycle" measures declared human review actions and whether
        # reinforced fading memories are recallable again afterwards.
        "lifecycle": {
            "review_distribution": review_dist,
            "fading_recovery_rate": _rate(sum(recoveries), len(recoveries)),
        },
    }


async def seed_demo_completion(embedder_mode: str = "hash") -> dict:
    """Product-surface check: does the seeded demo complete end-to-end on a
    fresh store? Returns counts only (no content)."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    engine = MemoryEngine(db_path=db_path, embedder_mode=embedder_mode)
    await engine.initialize()
    try:
        result = await engine.seed_demo()
        return {
            "completed": result.get("seeded", 0) > 0 and not result.get("skipped"),
            "memories": result.get("seeded", 0),
            "conflict_candidates": result.get("conflict_candidates", 0),
        }
    finally:
        await engine.shutdown()
        try:
            os.unlink(db_path)
        except OSError:
            pass
