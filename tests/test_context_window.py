"""Token budgeting and the query-aware context window.

Two contracts are pinned here.

**Backward compatibility.** ``get_context`` without a query must behave exactly
as it did before the token-budget work: recent short-term, then pinned, then
important episodic. Three callers in the suite (and every existing agent) rely on
that ordering, so the tokens module and the query path are additive.

**The new behaviour.** With a query, candidates are ranked by the same
``H(x,ψ)`` score ``recall_memory`` uses and packed into the budget in score
order — so a memory that is neither recent nor pinned can reach the window when
it is relevant, which the layered ordering could never do.
"""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core.memory_engine import MemoryEngine
from server.core.tokens import MAX_MEMORY_TOKENS, estimate_tokens, memory_tokens


@pytest_asyncio.fixture
async def engine():
    """Engine on a temporary DB with the deterministic hash embedder."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    eng = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=50)
    await eng.initialize()
    yield eng
    await eng.shutdown()
    if os.path.exists(db_path):
        os.unlink(db_path)


# ── token estimation ───────────────────────────────────────────────


def test_estimate_tokens_empty_is_zero():
    assert estimate_tokens("") == 0
    assert estimate_tokens("   \n\t ") == 0
    assert estimate_tokens(None) == 0


def test_estimate_tokens_prose_is_higher_than_dense_code():
    """Code and identifiers tokenize denser than prose, which is the whole
    reason ``len // 4`` was replaced."""
    prose = "the quick brown fox jumps over the lazy dog and then goes home"
    code = "MemoryEngine.initialize(self,db_path,embedder_mode)"
    prose_density = estimate_tokens(prose) / len(prose)
    code_density = estimate_tokens(code) / len(code)
    assert code_density > prose_density


def test_estimate_tokens_grows_with_length():
    short = estimate_tokens("auth token")
    long = estimate_tokens("auth token " * 40)
    assert long > short
    assert short >= 1


def test_estimate_tokens_is_deterministic():
    text = "Deploy branch is main; the JWT secret rotates weekly."
    assert estimate_tokens(text) == estimate_tokens(text)


def test_memory_tokens_is_capped():
    """One long memory must not be able to consume a whole window."""
    huge = "word " * (MAX_MEMORY_TOKENS * 10)
    assert memory_tokens(huge) == MAX_MEMORY_TOKENS
    assert memory_tokens("a short memory") < MAX_MEMORY_TOKENS


# ── backward compatibility: the layered path ───────────────────────


@pytest.mark.asyncio
async def test_context_without_query_keeps_layered_behaviour(engine):
    """No query ⇒ the original ordering, unchanged."""
    await engine.store(content="Context line 1", memory_type="short_term")
    await engine.store(content="Context line 2", memory_type="short_term")

    ctx = await engine.get_context(max_tokens=500)
    assert "Context line 1" in ctx
    assert "Context line 2" in ctx


@pytest.mark.asyncio
async def test_context_without_query_reports_layered_mode(engine):
    await engine.store(content="Something recent", memory_type="short_term")
    packing = await engine.get_context_packing(max_tokens=500)
    assert packing.mode == "layered"
    assert packing.max_tokens == 500


@pytest.mark.asyncio
async def test_context_still_isolates_sessions(engine):
    """The session filter must survive the refactor."""
    await engine.store(content="Context S1", session_id="s1", memory_type="short_term")
    await engine.store(content="Context S2", session_id="s2", memory_type="short_term")

    ctx = await engine.get_context(session_id="s1")
    assert "Context S1" in ctx
    assert "Context S2" not in ctx


@pytest.mark.asyncio
async def test_empty_context_returns_empty_string(engine):
    assert await engine.get_context(max_tokens=500) == ""


# ── the query-aware path ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_surfaces_a_memory_the_layered_order_would_miss(engine):
    """The payoff: relevance beats recency.

    The relevant memory is old and unpinned, and then fifteen newer short-term
    memories push it out of the layered window's reach. Only the query path can
    bring it back.
    """
    await engine.store(
        content="Deployment runbook: the production deploy branch is release-42.",
        memory_type="episodic",
        importance=0.5,
        project="ops",
    )
    for i in range(15):
        await engine.store(content=f"Unrelated chatter {i}", memory_type="short_term")

    query = "which branch does production deploy use"

    layered = await engine.get_context(max_tokens=4000, query=None)
    assert "release-42" not in layered

    focused = await engine.get_context(max_tokens=4000, query=query)
    assert "release-42" in focused


@pytest.mark.asyncio
async def test_query_mode_is_reported(engine):
    await engine.store(content="Deploy branch is main", memory_type="episodic")
    packing = await engine.get_context_packing(max_tokens=1000, query="deploy branch")
    assert packing.mode == "ranked"


@pytest.mark.asyncio
async def test_query_packing_respects_the_token_budget(engine):
    """The budget is enforced: the window must not exceed what was asked for."""
    for i in range(40):
        await engine.store(
            content=f"Memory number {i} about the deployment pipeline and its branches " * 3,
            memory_type="episodic",
            importance=0.9,
        )

    packing = await engine.get_context_packing(max_tokens=200, query="deployment pipeline")
    assert packing.included, "the budget should admit at least one memory"
    assert packing.omitted, "40 large memories cannot all fit in 200 tokens"
    # The packing must never exceed the budget it was given. `used_tokens` is the
    # figure the packer itself tracked, so this pins the contract rather than
    # re-deriving it from the rendered text.
    assert packing.used_tokens <= 200


@pytest.mark.asyncio
async def test_query_packing_reports_what_it_dropped(engine):
    for i in range(30):
        await engine.store(
            content=f"Long memory {i} " + "padding words " * 30,
            memory_type="episodic",
            importance=0.9,
        )
    packing = await engine.get_context_packing(max_tokens=50, query="long memory")

    assert set(packing.included).isdisjoint(packing.omitted), (
        "a memory cannot be both included and omitted"
    )
    assert len(packing.omitted) > 0


@pytest.mark.asyncio
async def test_pinned_memory_is_included_even_on_a_tiny_budget(engine):
    """A rule the user pinned must not be dropped by budgeting."""
    pinned = await engine.store(
        content="PINNED RULE: never force-push to main.",
        memory_type="episodic",
        importance=0.5,
        pinned=True,
    )
    for i in range(20):
        await engine.store(content=f"Filler {i} " * 20, memory_type="episodic", importance=0.9)

    packing = await engine.get_context_packing(max_tokens=1, query="unrelated topic")
    assert pinned.id in packing.included
    assert "never force-push to main" in packing.text


@pytest.mark.asyncio
async def test_query_path_isolates_sessions(engine):
    await engine.store(
        content="Deploy branch is release-42", session_id="s1", memory_type="episodic"
    )
    await engine.store(
        content="Deploy branch is other-99", session_id="s2", memory_type="episodic"
    )
    packing = await engine.get_context_packing(
        max_tokens=1000, session_id="s1", query="deploy branch"
    )
    assert "release-42" in packing.text
    assert "other-99" not in packing.text


@pytest.mark.asyncio
async def test_query_path_isolates_projects(engine):
    await engine.store(
        content="Deploy branch is alpha-1", project="alpha", memory_type="episodic"
    )
    await engine.store(
        content="Deploy branch is beta-2", project="beta", memory_type="episodic"
    )
    packing = await engine.get_context_packing(
        max_tokens=1000, project="alpha", query="deploy branch"
    )
    assert "alpha-1" in packing.text
    assert "beta-2" not in packing.text


@pytest.mark.asyncio
async def test_blank_query_falls_back_to_layered(engine):
    """Whitespace is not a topic; it must not silently switch modes."""
    await engine.store(content="Recent note", memory_type="short_term")
    packing = await engine.get_context_packing(max_tokens=500, query="   ")
    assert packing.mode == "layered"


@pytest.mark.asyncio
async def test_query_path_does_not_raise_on_an_empty_store(engine):
    packing = await engine.get_context_packing(max_tokens=500, query="anything")
    assert packing.text == ""
    assert packing.included == []


# ── regressions found in review (PR #370) ──────────────────────────


@pytest.mark.asyncio
async def test_layered_included_names_every_admitted_memory(engine):
    """`included` must describe the window, not just its pinned entries.

    Review finding: the layered path reported only pinned ids while short-term
    and episodic memories were also in the text, so a caller asking for the
    packing metadata got a list that did not match what it received.
    """
    short = await engine.store(content="A recent short-term note", memory_type="short_term")
    pin = await engine.store(
        content="A pinned rule", memory_type="episodic", pinned=True, importance=0.5
    )
    important = await engine.store(
        content="An important episodic fact", memory_type="episodic", importance=0.9
    )

    packing = await engine.get_context_packing(max_tokens=2000)
    assert packing.mode == "layered"
    # Every id reported must actually appear in the text's sources.
    for memory_id in (short.id, pin.id, important.id):
        assert memory_id in packing.included
    assert packing.included[0] == short.id, "included should follow admission order"


@pytest.mark.asyncio
async def test_capped_memory_is_admitted_at_the_size_it_is_charged(engine):
    """The per-memory cap must not let one outlier smuggle in its whole length.

    Review finding: `memory_tokens` capped the *cost* at MAX_MEMORY_TOKENS while
    the whole `content` was appended, so a memory far larger than the cap was
    charged 2000 tokens but contributed all of its 45,000 characters to the
    window — the cap hid the real cost instead of bounding it.

    The budget here is deliberately *above* the cap (2500 > 2000) so the
    oversized memories are admitted: the assertion is about the size of what was
    admitted, not about whether it fits.
    """
    await engine.store(content="oversized " * 4500, memory_type="episodic", importance=0.9)
    await engine.store(content="oversized " * 4500, memory_type="episodic", importance=0.9)

    packing = await engine.get_context_packing(max_tokens=2500, query="oversized")
    assert packing.included, "both memories should be admitted at the cap"
    # 2500 tokens is ~10,000 characters. Without truncation the two full
    # memories alone would be 45,000 characters.
    assert len(packing.text) <= packing.max_tokens * 4 * 2


@pytest.mark.asyncio
async def test_retired_memory_never_enters_the_window(monkeypatch):
    """A superseded fact must not reach the window.

    Review finding: the ranked predicate omitted the current-row check that
    `recall` applies through `_valid_at`, so a retired row could enter from the
    short-term deque or the vector store.

    Retirement (#335) is opt-in, so the flag is turned on here rather than
    inheriting the off-by-default value.
    """
    monkeypatch.setenv("LEVH_SUPERSESSION", "1")
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    eng = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=50)
    await eng.initialize()
    try:
        old = await eng.store(
            content="The production deploy branch is main",
            memory_type="episodic",
            importance=0.9,
        )
        await eng.store(
            content="The production deploy branch is main",
            memory_type="episodic",
            importance=0.9,
        )
        retired = await eng.get_memory(old.id)
        assert retired.valid_to is not None, (
            "the fixture must actually retire the first row or it proves nothing"
        )

        packing = await eng.get_context_packing(
            max_tokens=1000, query="production deploy branch"
        )
        assert old.id not in packing.included
    finally:
        await eng.shutdown()
        if os.path.exists(db_path):
            os.unlink(db_path)


@pytest.mark.asyncio
async def test_ranked_pool_reaches_an_old_low_importance_match(engine):
    """Pool breadth is the feature: relevance must beat recency *and* importance.

    Review finding: the ranked pool was only the 50 most recent short-term
    memories plus episodic rows with `min_importance >= 0.5`, so an older,
    low-importance memory could never be admitted however well it matched.
    """
    await engine.store(
        content="The staging rollback command is levh rollback --to staging-copy.",
        memory_type="episodic",
        importance=0.05,
        project="ops",
    )
    # Push it well outside the recent window and above it in importance.
    for i in range(60):
        await engine.store(
            content=f"Recent high-importance note {i} about unrelated topics",
            memory_type="episodic",
            importance=0.95,
        )

    packing = await engine.get_context_packing(
        max_tokens=4000, query="staging rollback command", project="ops"
    )
    assert "levh rollback --to staging-copy" in packing.text
