"""Score Breakdown Endpoint Tests — focused coverage for GET /api/memories/{id}/score-breakdown.

Tests cover:
  1. Existing memory returns 200 with full schema
  2. Missing memory returns 404
  3. Empty query does not crash (uses memory content as fallback)
  4. Component values are numeric and non-negative
  5. Score is numeric and between 0 and 1
  6. Component sum approximately equals score
"""

import asyncio
import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from httpx import AsyncClient, ASGITransport
from server.core.memory_engine import MemoryEngine


async def reset_api_engine():
    """Reset global API engine to prevent process hang."""
    import server.api as api_mod
    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    api_mod._engine = None
    api_mod._initialized = False


@pytest_asyncio.fixture
async def api_client():
    """Create an initialized API client with a temp DB and hash embedder."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)

    import server.api as api_mod
    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    api_mod._engine = MemoryEngine(
        db_path=db_path,
        embedder_mode="hash",
        short_term_max=50,
    )
    await api_mod._engine.initialize()
    api_mod._initialized = True

    from server.api import app
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await reset_api_engine()
    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest_asyncio.fixture
async def stored_memory(api_client):
    """Store a test memory and return its ID."""
    r = await api_client.post("/api/memories", json={
        "content": "Test memory for score breakdown",
        "importance": 0.7,
        "tags": ["test", "breakdown"],
        "memory_type": "episodic",
    })
    assert r.status_code == 200
    return r.json()["id"]


class TestScoreBreakdownHappyPath:
    """Test 1: Existing memory returns 200 with complete schema."""

    @pytest.mark.asyncio
    async def test_existing_memory_returns_200_with_schema(self, api_client, stored_memory):
        r = await api_client.get(
            f"/api/memories/{stored_memory}/score-breakdown",
            params={"query": "test breakdown"},
        )
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

        body = r.json()
        # Top-level fields
        assert "score" in body
        assert "components" in body
        assert "weights" in body

        # Components sub-fields
        comps = body["components"]
        assert "similarity_penalty" in comps
        assert "decay_penalty" in comps
        assert "importance_penalty" in comps
        assert "frequency_penalty" in comps

        # Weights sub-fields
        weights = body["weights"]
        assert "alpha" in weights
        assert "beta" in weights
        assert "gamma" in weights
        assert "delta" in weights

        # Weights should be default values
        assert weights["alpha"] == 0.4
        assert weights["beta"] == 0.2
        assert weights["gamma"] == 0.3
        assert weights["delta"] == 0.1


class TestScoreBreakdownNotFound:
    """Test 2: Missing memory returns 404."""

    @pytest.mark.asyncio
    async def test_missing_memory_returns_404(self, api_client):
        r = await api_client.get(
            "/api/memories/nonexistent-memory-id/score-breakdown",
            params={"query": "test"},
        )
        assert r.status_code == 404, f"Expected 404, got {r.status_code}"


class TestScoreBreakdownEmptyQuery:
    """Test 3: Empty query does not crash — uses memory content as fallback."""

    @pytest.mark.asyncio
    async def test_empty_query_returns_200(self, api_client, stored_memory):
        """Empty query string should still return 200 (memory content used as fallback)."""
        r = await api_client.get(
            f"/api/memories/{stored_memory}/score-breakdown",
            params={"query": ""},
        )
        assert r.status_code == 200, f"Empty query crashed: {r.status_code} {r.text}"
        body = r.json()
        assert "score" in body
        assert "components" in body
        assert "weights" in body

    @pytest.mark.asyncio
    async def test_whitespace_query_returns_200(self, api_client, stored_memory):
        """Whitespace-only query should also be treated as empty and return 200."""
        r = await api_client.get(
            f"/api/memories/{stored_memory}/score-breakdown",
            params={"query": "   "},
        )
        assert r.status_code == 200, f"Whitespace query crashed: {r.status_code} {r.text}"

    @pytest.mark.asyncio
    async def test_no_query_param_returns_200(self, api_client, stored_memory):
        """Omitting query param entirely should also return 200 (default is empty string)."""
        r = await api_client.get(
            f"/api/memories/{stored_memory}/score-breakdown",
        )
        assert r.status_code == 200, f"No query param crashed: {r.status_code} {r.text}"


class TestScoreBreakdownNumericBounds:
    """Test 4 & 5: Component values are numeric/non-negative; score in [0, 1]."""

    @pytest.mark.asyncio
    async def test_components_are_numeric_and_non_negative(self, api_client, stored_memory):
        r = await api_client.get(
            f"/api/memories/{stored_memory}/score-breakdown",
            params={"query": "test"},
        )
        assert r.status_code == 200
        body = r.json()

        # All components must be numeric (int or float)
        comps = body["components"]
        for key in ["similarity_penalty", "decay_penalty", "importance_penalty", "frequency_penalty"]:
            val = comps[key]
            assert isinstance(val, (int, float)), f"{key} is not numeric: {type(val)}"
            assert val >= 0, f"{key} is negative: {val}"

        # Weights must also be numeric and non-negative
        weights = body["weights"]
        for key in ["alpha", "beta", "gamma", "delta"]:
            val = weights[key]
            assert isinstance(val, (int, float)), f"weight {key} is not numeric"
            assert val >= 0, f"weight {key} is negative: {val}"

    @pytest.mark.asyncio
    async def test_score_between_0_and_1(self, api_client, stored_memory):
        r = await api_client.get(
            f"/api/memories/{stored_memory}/score-breakdown",
            params={"query": "test"},
        )
        assert r.status_code == 200
        score = r.json()["score"]
        assert isinstance(score, (int, float)), f"score is not numeric: {type(score)}"
        assert 0.0 <= score <= 1.0, f"score {score} is out of [0, 1] range"


class TestScoreBreakdownComponentSum:
    """Test 6: Component sum approximately equals total score."""

    @pytest.mark.asyncio
    async def test_component_sum_equals_score(self, api_client, stored_memory):
        r = await api_client.get(
            f"/api/memories/{stored_memory}/score-breakdown",
            params={"query": "test"},
        )
        assert r.status_code == 200
        body = r.json()

        score = body["score"]
        comps = body["components"]
        component_sum = (
            comps["similarity_penalty"]
            + comps["decay_penalty"]
            + comps["importance_penalty"]
            + comps["frequency_penalty"]
        )

        # Allow floating point rounding tolerance (within 0.001)
        assert abs(score - component_sum) < 0.001, (
            f"Score {score} != component sum {component_sum} "
            f"(diff={abs(score - component_sum):.6f})"
        )
