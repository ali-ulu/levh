"""Tests for v2 features: projects, sources, pinning, recall correctness,
session-filtered recall, env-configurable weights, schema migration,
context file generation, dedupe, and the new REST endpoints."""

import os
import sqlite3
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core.hscore import HScoreCalculator, HScoreWeights
from server.core.memory_engine import MemoryEngine
from server.core.types import Memory, MemoryType
from server.core.vector_store import VectorStore


@pytest_asyncio.fixture
async def engine():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    eng = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=10)
    await eng.initialize()
    yield eng
    await eng.shutdown()
    if os.path.exists(db_path):
        os.unlink(db_path)


# ── Projects & sources ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_project_namespace(engine):
    await engine.store(content="Project A fact", project="proj-a", memory_type="episodic")
    await engine.store(content="Project B fact", project="proj-b", memory_type="episodic")

    a_only = await engine.list_memories(project="proj-a")
    assert len(a_only) == 1
    assert a_only[0].project == "proj-a"

    projects = await engine.list_projects()
    names = {p["name"] for p in projects}
    assert names == {"proj-a", "proj-b"}
    assert all(p["memory_count"] == 1 for p in projects)


@pytest.mark.asyncio
async def test_source_tracking(engine):
    await engine.store(content="From Claude", source="claude-code", memory_type="episodic")
    await engine.store(content="From Cursor", source="cursor", memory_type="episodic")
    await engine.store(content="Also Claude", source="claude-code", memory_type="episodic")

    sources = await engine.list_sources()
    by_name = {s["name"]: s["memory_count"] for s in sources}
    assert by_name == {"claude-code": 2, "cursor": 1}

    claude_only = await engine.list_memories(source="claude-code")
    assert len(claude_only) == 2


@pytest.mark.asyncio
async def test_project_filtered_recall(engine):
    for i in range(20):
        await engine.store(content=f"Noise memory {i}", project="noise", memory_type="episodic")
    target = await engine.store(content="Target fact", project="target", memory_type="episodic")

    result = await engine.recall(query="Target fact", top_k=5, project="target")
    assert len(result.memories) == 1
    assert result.memories[0].id == target.id


# ── Pinning ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pin_unpin(engine):
    mem = await engine.store(content="Pin me", memory_type="episodic")
    assert mem.pinned is False

    pinned = await engine.set_pinned(mem.id, True)
    assert pinned.pinned is True

    fetched = await engine.get_memory(mem.id)
    assert fetched.pinned is True

    pinned_list = await engine.list_memories(pinned=True)
    assert [m.id for m in pinned_list] == [mem.id]

    unpinned = await engine.set_pinned(mem.id, False)
    assert unpinned.pinned is False


@pytest.mark.asyncio
async def test_pinned_exempt_from_decay(engine):
    old_ts = "2020-01-01T00:00:00+00:00"
    mem = await engine.store(content="Ancient pinned wisdom", memory_type="episodic", pinned=True)
    # Backdate last access so decay would normally crush the score
    await engine.db.update_memory(mem.id, {"accessed_at": old_ts})
    engine.vector_store.get(mem.id).accessed_at = old_ts

    result = await engine.recall(query="Ancient pinned wisdom", top_k=1)
    assert result.memories, "pinned memory must still be recallable"
    pinned_score = result.scores[0]

    # Same-age unpinned memory must score strictly worse (higher H-score)
    mem2 = await engine.store(content="Ancient plain fact xyz", memory_type="episodic")
    await engine.db.update_memory(mem2.id, {"accessed_at": old_ts})
    engine.vector_store.get(mem2.id).accessed_at = old_ts
    result2 = await engine.recall(query="Ancient plain fact xyz", top_k=1)
    assert result2.scores[0] > pinned_score


@pytest.mark.asyncio
async def test_pinned_never_auto_deduped(engine):
    a = await engine.store(content="Exact duplicate content", memory_type="episodic", pinned=True)
    b = await engine.store(content="Exact duplicate content", memory_type="episodic", pinned=True)
    removed = await engine.dedupe(similarity_threshold=0.99)
    assert removed == 0
    assert await engine.get_memory(a.id) is not None
    assert await engine.get_memory(b.id) is not None


# ── Recall correctness ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recall_touches_only_returned(engine):
    memories = []
    for i in range(8):
        memories.append(
            await engine.store(content=f"Distinct content number {i}", memory_type="episodic")
        )

    result = await engine.recall(query="Distinct content number 0", top_k=2)
    returned_ids = {m.id for m in result.memories}
    assert len(returned_ids) == 2

    for m in memories:
        fresh = await engine.get_memory(m.id)
        if m.id in returned_ids:
            assert fresh.frequency == 2, "returned memories get frequency bumped"
        else:
            assert fresh.frequency == 1, "non-returned candidates must NOT be touched"


@pytest.mark.asyncio
async def test_session_filtered_recall_not_starved(engine):
    """Session filter applies before ranking, so results are found even when
    other sessions dominate the vector space."""
    for i in range(30):
        await engine.store(content=f"Other session memory {i}", session_id="other", memory_type="episodic")
    target = await engine.store(content="My session note", session_id="mine", memory_type="episodic")

    result = await engine.recall(query="memory note", top_k=5, session_id="mine")
    assert [m.id for m in result.memories] == [target.id]


# ── Reinforcement / adaptive decay (human-memory model) ─────────────


@pytest.mark.asyncio
async def test_recall_reinforces_stability(engine):
    """Each recall should strengthen a memory's stability (half-life) and
    bump its recall_count — modeling spaced repetition / the testing effect."""
    mem = await engine.store(content="Reinforcement target fact", importance=0.5, memory_type="episodic")
    initial_stability = mem.stability_hours
    assert mem.recall_count == 0

    await engine.recall(query="Reinforcement target fact", top_k=1)
    once = await engine.get_memory(mem.id)
    assert once.stability_hours > initial_stability
    assert once.recall_count == 1

    await engine.recall(query="Reinforcement target fact", top_k=1)
    twice = await engine.get_memory(mem.id)
    assert twice.stability_hours > once.stability_hours
    assert twice.recall_count == 2


@pytest.mark.asyncio
async def test_higher_importance_reinforces_more(engine):
    """More important memories should consolidate faster per recall —
    like emotionally salient events being remembered more easily."""
    low = await engine.store(content="Low importance fact alpha", importance=0.1, memory_type="episodic")
    high = await engine.store(content="High importance fact beta", importance=0.9, memory_type="episodic")
    # Snapshot the starting values now — the vector store holds these exact
    # objects by reference, so reading .stability_hours off `low`/`high`
    # again *after* recall would see the already-mutated value.
    low_initial, high_initial = low.stability_hours, high.stability_hours

    await engine.recall(query="Low importance fact alpha", top_k=1)
    await engine.recall(query="High importance fact beta", top_k=1)

    low_after = await engine.get_memory(low.id)
    high_after = await engine.get_memory(high.id)
    low_growth = low_after.stability_hours / low_initial
    high_growth = high_after.stability_hours / high_initial
    assert high_growth > low_growth


@pytest.mark.asyncio
async def test_decay_measured_from_last_access_not_creation(engine):
    """A memory created long ago but recalled recently should score as fresh —
    recalling something resets how 'faded' it feels, exactly like human recall."""
    old_ts = "2020-01-01T00:00:00+00:00"
    mem = await engine.store(content="Old but recently recalled fact", memory_type="episodic")
    # Backdate creation only — accessed_at stays recent (as if just recalled).
    await engine.db.update_memory(mem.id, {"created_at": old_ts})
    engine.vector_store.get(mem.id).created_at = old_ts

    result = await engine.recall(query="Old but recently recalled fact", top_k=1)
    assert result.scores[0] < 0.3, "decay should be negligible since last access was recent"


@pytest.mark.asyncio
async def test_reinforce_memory_manual(engine):
    mem = await engine.store(content="Manually reinforce me", memory_type="episodic")
    initial = mem.stability_hours

    reinforced = await engine.reinforce_memory(mem.id)
    assert reinforced.stability_hours > initial
    assert reinforced.recall_count == 1

    assert await engine.reinforce_memory("nonexistent-id") is None


@pytest.mark.asyncio
async def test_reinforcement_capped_at_max_stability(engine):
    mem = await engine.store(content="Reinforce many times", importance=1.0, memory_type="episodic")
    for _ in range(60):
        mem = await engine.reinforce_memory(mem.id)
    assert mem.stability_hours <= engine.scorer.max_stability_hours


@pytest.mark.asyncio
async def test_forgetting_curve_shape(engine):
    mem = await engine.store(content="Curve test fact", memory_type="episodic")
    curve = await engine.get_forgetting_curve(mem.id, days=30)
    assert curve is not None
    points = curve["curve"]
    assert points[0]["day"] == 0
    assert points[0]["retention"] == pytest.approx(1.0, abs=0.01)
    # Monotonically non-increasing retention over time
    retentions = [p["retention"] for p in points]
    assert all(a >= b for a, b in zip(retentions, retentions[1:]))
    # At exactly one stability period out, retention should be ~50%
    half_life_days = mem.stability_hours / 24
    if half_life_days <= 30:
        closest = min(points, key=lambda p: abs(p["day"] - half_life_days))
        assert closest["retention"] == pytest.approx(0.5, abs=0.05)

    assert await engine.get_forgetting_curve("nonexistent-id") is None


# ── Feedback learning (SM-2 style) ─────────────────────────────────


@pytest.mark.asyncio
async def test_feedback_helpful_reinforces(engine):
    mem = await engine.store(content="Helpful fact xyz", memory_type="episodic")
    initial = mem.stability_hours
    updated = await engine.memory_feedback(mem.id, helpful=True)
    assert updated.stability_hours > initial
    assert updated.recall_count == 1


@pytest.mark.asyncio
async def test_feedback_unhelpful_weakens(engine):
    mem = await engine.store(content="Stale fact xyz", memory_type="episodic")
    initial = mem.stability_hours
    updated = await engine.memory_feedback(mem.id, helpful=False)
    assert updated.stability_hours < initial
    assert updated.recall_count == 0, "negative feedback is not a successful recall"

    # Weakening floors at 1 hour instead of vanishing instantly
    for _ in range(20):
        updated = await engine.memory_feedback(mem.id, helpful=False)
    assert updated.stability_hours == 1.0

    assert await engine.memory_feedback("nonexistent-id", helpful=True) is None


# ── Retroactive interference ───────────────────────────────────────


@pytest.mark.asyncio
async def test_interference_weakens_superseded_memory(engine):
    old = await engine.store(content="The deploy branch is main", memory_type="episodic")
    old_initial = old.stability_hours

    # Near-identical new memory supersedes the old one
    await engine.store(content="The deploy branch is prod", memory_type="episodic")

    weakened = await engine.get_memory(old.id)
    assert weakened.stability_hours < old_initial


@pytest.mark.asyncio
async def test_interference_immune_pinned_and_other_projects(engine):
    pinned = await engine.store(content="Immutable rule text here", pinned=True, memory_type="episodic")
    other_project = await engine.store(
        content="Same content different world", project="proj-a", memory_type="episodic"
    )
    p_initial, o_initial = pinned.stability_hours, other_project.stability_hours

    await engine.store(content="Immutable rule text here", memory_type="episodic")
    await engine.store(content="Same content different world", project="proj-b", memory_type="episodic")

    assert (await engine.get_memory(pinned.id)).stability_hours == p_initial
    assert (await engine.get_memory(other_project.id)).stability_hours == o_initial


# ── Fading memories review queue ───────────────────────────────────


@pytest.mark.asyncio
async def test_list_fading(engine):
    old_ts = "2020-01-01T00:00:00+00:00"
    faded = await engine.store(content="Long forgotten trivia", memory_type="episodic")
    await engine.db.update_memory(faded.id, {"accessed_at": old_ts})

    fresh = await engine.store(content="Fresh important thing", memory_type="episodic")
    pinned = await engine.store(
        content="Pinned forever", pinned=True, memory_type="episodic"
    )
    await engine.db.update_memory(pinned.id, {"accessed_at": old_ts})

    fading = await engine.list_fading(threshold=0.35)
    ids = [m.id for m, _ in fading]
    assert faded.id in ids, "stale memory must appear in the fading queue"
    assert fresh.id not in ids, "fresh memory must not be fading"
    assert pinned.id not in ids, "pinned memories never fade"
    retention = dict((m.id, r) for m, r in fading)[faded.id]
    assert retention < 0.01


# ── Env-configurable scoring ───────────────────────────────────────


def test_hscore_weights_from_env(monkeypatch):
    monkeypatch.setenv("HSCORE_ALPHA", "0.7")
    monkeypatch.setenv("HSCORE_BETA", "0.1")
    monkeypatch.setenv("HSCORE_GAMMA", "0.1")
    monkeypatch.setenv("HSCORE_DELTA", "0.1")
    monkeypatch.setenv("DECAY_HALF_LIFE_HOURS", "24")
    calc = HScoreCalculator()
    assert calc.w.alpha == 0.7
    assert calc.half_life_hours == 24.0


def test_hscore_weights_env_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("HSCORE_ALPHA", "not-a-number")
    calc = HScoreCalculator()
    assert calc.w.alpha == 0.4


# ── Vector store dimension tolerance ───────────────────────────────


def test_vector_store_mixed_dimensions():
    store = VectorStore(dimension=4)
    m_small = Memory(content="small", embedding=[1.0, 0.0, 0.0, 0.0])
    m_large = Memory(content="large", embedding=[0.0] * 8)
    store.add(m_small)
    store.add(m_large)
    assert store.size == 2

    # 4-d query only matches the 4-d vector; must not crash on the 8-d one
    results = store.search([1.0, 0.0, 0.0, 0.0], top_k=5)
    assert [m.id for m, _ in results] == [m_small.id]


def test_vector_store_predicate():
    store = VectorStore()
    m1 = Memory(content="a", embedding=[1.0, 0.0], session_id="s1")
    m2 = Memory(content="b", embedding=[0.9, 0.1], session_id="s2")
    store.add(m1)
    store.add(m2)
    results = store.search([1.0, 0.0], top_k=5, predicate=lambda m: m.session_id == "s2")
    assert [m.id for m, _ in results] == [m2.id]


# ── Sessions ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_end_session_consolidates(engine):
    session = await engine.create_session(name="Work")
    await engine.store(content="Session note 1", session_id=session.id)
    await engine.store(content="Session note 2", session_id=session.id)
    assert len(engine.short_term) == 2

    ended = await engine.end_session(session.id)
    assert ended.status.value == "ended"
    assert ended.memory_count == 2
    assert len(engine.short_term) == 0, "short-term memories consolidated on end"

    consolidated = await engine.list_memories(session_id=session.id, memory_type="episodic")
    assert len(consolidated) == 2


@pytest.mark.asyncio
async def test_store_updates_session_count(engine):
    session = await engine.create_session(name="Counted")
    await engine.store(content="one", session_id=session.id)
    await engine.store(content="two", session_id=session.id)
    fetched = await engine.get_session(session.id)
    assert fetched.memory_count == 2


# ── Store semantics ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_episodic_store_skips_short_term_deque(engine):
    await engine.store(content="Long-term fact", memory_type="episodic")
    assert len(engine.short_term) == 0
    await engine.store(content="Live note", memory_type="short_term")
    assert len(engine.short_term) == 1


# ── Context file generation ────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_context_file(engine):
    await engine.store(content="Always use tabs", project="p", pinned=True, memory_type="episodic")
    await engine.store(content="API key lives in .env", project="p", importance=0.9, memory_type="episodic")
    await engine.store(content="Yesterday we fixed the login bug", project="p", memory_type="episodic")

    content = await engine.generate_context_file(project="p")
    assert "Always Remember" in content
    assert "Always use tabs" in content
    assert "Key Decisions" in content
    assert "API key lives in .env" in content
    assert "Recent Context" in content

    empty = await engine.generate_context_file(project="nonexistent")
    assert "No memories stored yet" in empty


# ── Dedupe ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dedupe_removes_duplicates(engine):
    keep = await engine.store(content="Duplicate content here", importance=0.9, memory_type="episodic")
    await engine.store(content="Duplicate content here", importance=0.3, memory_type="episodic")
    await engine.store(content="Totally different thing", memory_type="episodic")

    groups = await engine.find_duplicates(similarity_threshold=0.99)
    assert len(groups) == 1

    removed = await engine.dedupe(similarity_threshold=0.99)
    assert removed == 1
    assert await engine.get_memory(keep.id) is not None, "highest importance is kept"
    remaining = await engine.list_memories(limit=100)
    assert len(remaining) == 2


# ── Schema migration ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_migration_from_v1_schema():
    """A v1.0 database (without project/source/pinned) must upgrade cleanly."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY, content TEXT NOT NULL,
            memory_type TEXT NOT NULL DEFAULT 'short_term',
            embedding TEXT, importance REAL DEFAULT 0.5,
            frequency INTEGER DEFAULT 1, tags TEXT, session_id TEXT,
            metadata TEXT, hscore REAL, created_at TEXT NOT NULL,
            accessed_at TEXT NOT NULL, decay_factor REAL DEFAULT 1.0
        );
        INSERT INTO memories (id, content, created_at, accessed_at)
        VALUES ('old1', 'legacy memory', '2025-01-01T00:00:00+00:00', '2025-01-01T00:00:00+00:00');
        """
    )
    con.commit()
    con.close()

    eng = MemoryEngine(db_path=db_path, embedder_mode="hash")
    await eng.initialize()
    try:
        legacy = await eng.get_memory("old1")
        assert legacy is not None
        assert legacy.pinned is False
        assert legacy.project is None
        # New-style store works against the migrated DB
        mem = await eng.store(content="new memory", project="p", pinned=True, memory_type="episodic")
        assert (await eng.get_memory(mem.id)).pinned is True
    finally:
        await eng.shutdown()
        os.unlink(db_path)


# ── New REST endpoints ─────────────────────────────────────────────


@pytest_asyncio.fixture
async def api_client():
    from httpx import ASGITransport, AsyncClient

    import server.api as api_mod

    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)

    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    api_mod._engine = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=50)
    await api_mod._engine.initialize()
    api_mod._initialized = True

    transport = ASGITransport(app=api_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await api_mod._engine.shutdown()
    api_mod._engine = None
    api_mod._initialized = False
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_api_projects_sources_tags(api_client):
    r = await api_client.post(
        "/api/memories",
        json={"content": "tagged", "tags": ["alpha", "beta"], "project": "proj-x",
              "source": "cursor", "memory_type": "episodic"},
    )
    assert r.status_code == 200

    r = await api_client.get("/api/projects")
    assert r.status_code == 200
    assert r.json()["projects"][0]["name"] == "proj-x"

    r = await api_client.get("/api/sources")
    assert [s["name"] for s in r.json()["sources"]] == ["cursor"]

    r = await api_client.get("/api/tags")
    assert {t["name"] for t in r.json()["tags"]} == {"alpha", "beta"}


@pytest.mark.asyncio
async def test_api_pin_endpoint(api_client):
    r = await api_client.post("/api/memories", json={"content": "pin via api", "memory_type": "episodic"})
    mem_id = r.json()["id"]

    r = await api_client.patch(f"/api/memories/{mem_id}/pin", json={"pinned": True})
    assert r.status_code == 200
    assert r.json()["pinned"] is True

    r = await api_client.patch("/api/memories/nope/pin", json={"pinned": True})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_api_context_file(api_client):
    await api_client.post(
        "/api/memories",
        json={"content": "Rule: no ORMs", "project": "p", "pinned": True, "memory_type": "episodic"},
    )
    r = await api_client.post("/api/context-file", json={"project": "p", "style": "claude"})
    assert r.status_code == 200
    body = r.json()
    assert body["filename"] == "CLAUDE.md"
    assert "Rule: no ORMs" in body["content"]


@pytest.mark.asyncio
async def test_api_config(api_client):
    r = await api_client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert "weights" in body and "db_path" in body
    assert body["short_term_max"] == 50


@pytest.mark.asyncio
async def test_api_list_filters(api_client):
    await api_client.post("/api/memories", json={"content": "find me please", "project": "p1", "memory_type": "episodic"})
    await api_client.post("/api/memories", json={"content": "other thing", "project": "p2", "memory_type": "episodic"})

    r = await api_client.get("/api/memories", params={"q": "find me"})
    assert len(r.json()) == 1

    r = await api_client.get("/api/memories", params={"project": "p2"})
    assert len(r.json()) == 1
    assert r.json()[0]["project"] == "p2"


@pytest.mark.asyncio
async def test_api_dedupe_endpoint(api_client):
    await api_client.post("/api/memories", json={"content": "dup dup dup", "memory_type": "episodic"})
    await api_client.post("/api/memories", json={"content": "dup dup dup", "memory_type": "episodic", "force": True})

    r = await api_client.post("/api/memories/dedupe", json={"similarity_threshold": 0.99, "dry_run": True})
    assert r.status_code == 200
    assert r.json()["duplicates"] == 1

    r = await api_client.post("/api/memories/dedupe", json={"similarity_threshold": 0.99, "dry_run": False})
    assert r.json()["removed"] == 1


@pytest.mark.asyncio
async def test_api_reinforce_endpoint(api_client):
    r = await api_client.post("/api/memories", json={"content": "reinforce via api", "memory_type": "episodic"})
    body = r.json()
    mem_id, initial_stability = body["id"], body["stability_hours"]

    r = await api_client.post(f"/api/memories/{mem_id}/reinforce")
    assert r.status_code == 200
    assert r.json()["stability_hours"] > initial_stability
    assert r.json()["recall_count"] == 1

    r = await api_client.post("/api/memories/nope/reinforce")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_api_forgetting_curve_endpoint(api_client):
    r = await api_client.post("/api/memories", json={"content": "curve via api", "memory_type": "episodic"})
    mem_id = r.json()["id"]

    r = await api_client.get(f"/api/memories/{mem_id}/forgetting-curve", params={"days": 14})
    assert r.status_code == 200
    body = r.json()
    assert body["curve"][0]["day"] == 0
    assert body["current_retention"] == pytest.approx(1.0, abs=0.01)
    assert "stability_hours" in body

    r = await api_client.get("/api/memories/nope/forgetting-curve")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_api_config_includes_reinforcement_settings(api_client):
    r = await api_client.get("/api/config")
    body = r.json()
    assert "reinforcement_gain" in body
    assert "max_stability_hours" in body


@pytest.mark.asyncio
async def test_api_feedback_endpoint(api_client):
    r = await api_client.post("/api/memories", json={"content": "feedback via api", "memory_type": "episodic"})
    body = r.json()
    mem_id, initial = body["id"], body["stability_hours"]

    r = await api_client.post(f"/api/memories/{mem_id}/feedback", json={"helpful": True})
    assert r.status_code == 200
    boosted = r.json()["stability_hours"]
    assert boosted > initial

    r = await api_client.post(f"/api/memories/{mem_id}/feedback", json={"helpful": False})
    assert r.json()["stability_hours"] < boosted

    r = await api_client.post("/api/memories/nope/feedback", json={"helpful": True})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_api_fading_endpoint(api_client):
    import server.api as api_mod

    r = await api_client.post("/api/memories", json={"content": "will fade away", "memory_type": "episodic"})
    mem_id = r.json()["id"]
    await api_mod._engine.db.update_memory(mem_id, {"accessed_at": "2020-01-01T00:00:00+00:00"})

    r = await api_client.get("/api/memories/fading")
    assert r.status_code == 200
    body = r.json()
    assert any(m["id"] == mem_id for m in body)
    assert all("retention" in m for m in body)


# ── New MCP tools registered ───────────────────────────────────────


@pytest.mark.asyncio
async def test_new_mcp_tools_registered():
    from mcp.server.fastmcp import FastMCP

    from server.tools.register import register_all_tools

    mcp = FastMCP("test")
    engine = MemoryEngine(db_path=":memory:", embedder_mode="hash")
    register_all_tools(mcp, engine)
    tool_names = {t.name for t in await mcp.list_tools()}
    for expected in [
        "pin_memory", "unpin_memory", "list_projects", "list_sources",
        "generate_context_file", "dedupe_memories", "reinforce_memory",
        "memory_feedback", "list_fading_memories",
    ]:
        assert expected in tool_names, f"Missing MCP tool: {expected}"
