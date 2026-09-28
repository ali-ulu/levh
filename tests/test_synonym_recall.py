"""Query-term expansion and the entity bridge.

Word-overlap ranking matches the words of the query against the words of a
memory. That finds an inflection ("migrasyon" -> "migrasyonu") but not a
synonym, and not a name: "how do users log in" shares no surface word with a
memory about JWT authentication, so the memory could never become a candidate
however well it answers the question. Expansion and the entity graph are the
two model-free bridges across that gap.

Offline and deterministic — ``EMBEDDER_MODE=hash``.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
import pytest_asyncio

os.environ["EMBEDDER_MODE"] = "hash"

from server.core import synonyms
from server.core.lexical import expand_terms, similarity_expanded, terms
from server.core.memory_engine import MemoryEngine
from server.core.synonyms import SynonymTable, parse_synonyms


@pytest_asyncio.fixture
async def engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = MemoryEngine(db_path=path, embedder_mode="hash", short_term_max=50)
    await eng.initialize()
    yield eng
    await eng.shutdown()
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def fresh_table(monkeypatch):
    """The table with no file override and no cached parse."""
    monkeypatch.setattr(synonyms, "_FILE_CACHE", {})
    monkeypatch.delenv("LEVH_SYNONYMS_PATH", raising=False)
    return SynonymTable.load()


# --- the table itself -----------------------------------------------------


def test_builtin_equivalences_are_symmetric():
    table = SynonymTable.load()
    assert "signin" in table.for_term("login")
    assert "login" in table.for_term("signin")


def test_file_groups_are_transitively_closed():
    """a~b and b~c must make a~c, or a pairwise file stops at one hop."""
    parsed = parse_synonyms([["a", "b"], ["b", "c"]])
    assert parsed["a"] == frozenset({"b", "c"})
    assert parsed["c"] == frozenset({"a", "b"})


def test_object_shape_is_accepted():
    parsed = parse_synonyms({"deploy": ["release"], "release": ["ship"]})
    assert "release" in parsed["deploy"] and "ship" in parsed["deploy"]


def test_groups_wrapper_is_accepted():
    assert parse_synonyms({"groups": [["deploy", "release"]]})["deploy"] == frozenset(
        {"release"}
    )


def test_malformed_entries_are_skipped_not_fatal():
    parsed = parse_synonyms({"ok": ["fine"], "bad": "not-a-list", 7: ["x"], "": ["y"]})
    assert "fine" in parsed["ok"]
    assert "not-a-list" not in parsed and "x" not in parsed and "y" not in parsed


def test_shipped_vocabulary_loads():
    """The packaged file is the out-of-the-box vocabulary; a syntax error in it
    would silently cost every store its expansions."""
    assert synonyms.default_path(), "shipped synonyms.json is missing"
    table = SynonymTable.load()
    assert "authentication" in table.for_term("login")
    assert "migrasyon" in table.for_term("migration")


def test_missing_file_falls_back_to_builtin(tmp_path):
    table = SynonymTable.load(str(tmp_path / "nope.json"))
    assert "signin" in table.for_term("login")


def test_broken_file_is_ignored(tmp_path, caplog):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    table = SynonymTable.load(str(bad))
    assert "signin" in table.for_term("login")


def test_user_file_is_merged_with_builtin(tmp_path):
    custom = tmp_path / "syn.json"
    custom.write_text(json.dumps({"zephyr": ["wind"]}), encoding="utf-8")
    table = SynonymTable.load(str(custom))
    assert table.for_term("zephyr") == frozenset({"wind"})
    assert "signin" in table.for_term("login")


def test_configured_path_env_wins(monkeypatch, tmp_path):
    custom = tmp_path / "syn.json"
    custom.write_text(json.dumps({"zephyr": ["wind"]}), encoding="utf-8")
    monkeypatch.setattr(synonyms, "_FILE_CACHE", {})
    monkeypatch.setenv("LEVH_SYNONYMS_PATH", str(custom))
    assert "wind" in SynonymTable.load().for_term("zephyr")


def test_file_is_reread_when_it_changes(tmp_path):
    """A vocabulary file is config: editing it must take effect without a
    process restart, or a user cannot fix a missing term while looking at it."""
    path = tmp_path / "syn.json"
    path.write_text(json.dumps({"zephyr": ["wind"]}), encoding="utf-8")
    assert SynonymTable.load(str(path)).for_term("zephyr") == frozenset({"wind"})
    path.write_text(json.dumps({"zephyr": ["breeze"]}), encoding="utf-8")
    os.utime(path, (0, 0))  # force a different mtime on coarse filesystems
    assert SynonymTable.load(str(path)).for_term("zephyr") == frozenset({"breeze"})


def test_multi_word_phrase_lends_its_group_to_the_query(fresh_table):
    """'log in' is one idea the tokenizer splits; without phrase handling the
    user's own wording expands to nothing."""
    expansions = fresh_table.expand("how do users log in")
    assert "authentication" in expansions.get("log", frozenset())


def test_phrase_only_matches_its_own_words(fresh_table):
    """'in' alone is not 'log in' — a phrase must match adjacent, not by token."""
    assert "in" not in fresh_table.expand("what is in the box")


# --- the score and the candidate set -------------------------------------


def test_expansion_does_not_inflate_unrelated_matches():
    """Expansion widens what a query term matches; it must not add extra
    matched terms, which would push every memory sharing a thesaurus entry up."""
    expansions = {"login": frozenset({"auth"})}
    assert similarity_expanded("login", "auth service", expansions) == 1.0
    assert similarity_expanded("login", "unrelated note", expansions) == 0.0
    # A memory that matches only via the synonym scores the same as one
    # matching the surface word — one covered query term either way.
    assert similarity_expanded("login", "auth", expansions) == similarity_expanded(
        "login", "login", expansions
    )


def test_expand_terms_adds_equivalents_and_keeps_originals():
    expanded = expand_terms("migration notes", {"migration": frozenset({"migrate"})})
    assert {"migration", "notes", "migrate"} <= expanded


def test_expand_terms_without_a_table_is_the_plain_terms():
    assert expand_terms("migration notes", None) == terms("migration notes")


# --- end to end through recall -------------------------------------------


@pytest.mark.asyncio
async def test_synonym_query_reaches_a_differently_worded_memory(engine):
    target = await engine.store(
        content="API authentication uses JWT tokens", memory_type="episodic"
    )
    await engine.store(content="The kiosk screen reboot script", memory_type="episodic")

    # "log in" leaves the token "log" without a table entry of its own; the
    # phrase lends it its group's words.
    result = await engine.recall("how do users log in", top_k=5, reinforce=False)

    assert result.memories, "the JWT memory did not surface"
    assert target.id in [m.id for m in result.memories[:2]]


@pytest.mark.asyncio
async def test_a_user_vocabulary_term_is_recallable(engine, monkeypatch, tmp_path):
    custom = tmp_path / "syn.json"
    custom.write_text(json.dumps({"zephyr": ["sprintboard"]}), encoding="utf-8")
    monkeypatch.setattr(synonyms, "_FILE_CACHE", {})
    monkeypatch.setenv("LEVH_SYNONYMS_PATH", str(custom))
    target = await engine.store(
        content="The sprintboard rotation is every monday", memory_type="episodic"
    )
    await engine.store(content="The office plants are watered friday", memory_type="episodic")

    result = await engine.recall("zephyr schedule", top_k=5, reinforce=False)

    assert target.id in [m.id for m in result.memories]


@pytest.mark.asyncio
async def test_synonyms_never_widen_the_duplicate_check(engine):
    """Two memories that merely use different words for the same idea are not
    duplicates. Applying the table to admission would start dropping real
    memories, so it stays a retrieval-only concern."""
    await engine.store(content="API authentication uses JWT tokens", memory_type="episodic")
    decision = await engine.evaluate_admission(
        content="The login flow is documented in the runbook"
    )
    assert decision["action"] == "admit"


@pytest.mark.asyncio
async def test_entity_name_bridges_to_its_memories(engine):
    """A query naming an entity reaches the memories the graph links to it.

    Tested on the bridge itself rather than through ranking: the target's
    content contains the entity name, so recall could also reach it by word
    overlap, and that would prove nothing about the graph path.
    """
    target = await engine.store(
        content="Zephyr Labs owns the billing rewrite", memory_type="episodic"
    )
    await engine.reindex_entities()

    linked = await engine._entity_linked_memories({"zephyr"}, set())

    assert target.id in [m.id for m in linked]


@pytest.mark.asyncio
async def test_unknown_query_term_links_to_nothing(engine):
    await engine.store(content="Zephyr Labs owns the billing rewrite", memory_type="episodic")
    await engine.reindex_entities()

    assert await engine._entity_linked_memories({"kayaking"}, set()) == []


@pytest.mark.asyncio
async def test_entity_candidates_are_labelled(engine):
    target = await engine.store(
        content="Zephyr Labs owns the billing rewrite", memory_type="episodic"
    )
    await engine.reindex_entities()

    result = await engine.recall(
        "what does Zephyr Labs own", top_k=5, reinforce=False, explain=True
    )

    by_id = {m.id: b for m, b in zip(result.memories, result.breakdowns)}
    assert by_id[target.id].candidate_source == "entity"


@pytest.mark.asyncio
async def test_fts_only_synonym_hit_scores_lexically_in_semantic_mode(engine):
    """A candidate reached only by the widened FTS query has no vector to
    compare, so reporting cosine 0 would bury it; coverage is reported instead."""
    target = await engine.store(
        content="API authentication uses JWT tokens", memory_type="episodic"
    )
    await engine.db.conn.execute(
        "UPDATE memories SET embedding = NULL WHERE id = ?", (target.id,)
    )
    await engine.db.commit()
    engine.vector_store.remove(target.id)

    result = await engine.recall("log in", top_k=5, reinforce=False, explain=True)

    assert target.id in [m.id for m in result.memories]
