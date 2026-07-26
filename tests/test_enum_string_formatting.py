"""`MemoryType`/`SessionStatus` must format as their plain value everywhere.

`(str, Enum)` alone is not enough: `Enum.__str__`/`__format__` print
"ClassName.MEMBER" regardless of the `str` mixin, unless overridden. That
leaked into MCP tool output text — the model's context and what a user reads
on screen — as e.g. "Type: MemoryType.EPISODIC" or "Status:
SessionStatus.ACTIVE". `tests/test_mcp_blackbox.py` proves the fix end-to-end
through the real protocol for store/recall; this file pins the root-cause
behaviour directly and covers the tool surfaces the black-box test doesn't
reach (list, search, session).
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from server.core.memory_engine import MemoryEngine
from server.core.types import MemoryType, SessionStatus


# ── the root-cause fix, in isolation ──────────────────────────────────


@pytest.mark.parametrize("member", list(MemoryType))
def test_memory_type_formats_as_its_plain_value(member):
    assert str(member) == member.value
    assert f"{member}" == member.value
    assert "MemoryType." not in str(member)
    # The enum/str duality that everything else depends on must survive.
    assert member == member.value
    assert isinstance(member, str)


@pytest.mark.parametrize("member", list(SessionStatus))
def test_session_status_formats_as_its_plain_value(member):
    assert str(member) == member.value
    assert f"{member}" == member.value
    assert "SessionStatus." not in str(member)
    assert member == member.value
    assert isinstance(member, str)


def test_model_dump_survives_the_format_fix_too():
    """`model_dump()` (Python mode) keeps the raw enum instance rather than
    its value — store.py formats that dict value directly, so the __str__
    override has to cover this shape as well, not just direct attribute
    access on the model."""
    from server.core.types import Memory

    memory = Memory(content="x", memory_type=MemoryType.EPISODIC)
    dumped = memory.model_dump()
    assert f"{dumped['memory_type']}" == "episodic"


# ── the tool surfaces the black-box test doesn't reach ────────────────


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = MemoryEngine(db_path=str(tmp_path / "enumfmt.db"), embedder_mode="hash")
    await eng.initialize()
    try:
        yield eng
    finally:
        await eng.shutdown()


@pytest.mark.asyncio
async def test_list_memories_tool_output_has_no_raw_enum_repr(engine):
    from server.tools.list_memories import register

    captured = {}

    class _FakeMCP:
        def tool(self, *_args, **_kwargs):
            def decorator(fn):
                captured[fn.__name__] = fn
                return fn

            return decorator

    register(_FakeMCP(), engine)
    await engine.store("A note for the list view", memory_type="episodic")

    text = await captured["list_memories"]()

    assert "MemoryType." not in text


@pytest.mark.asyncio
async def test_search_memory_tool_output_has_no_raw_enum_repr(engine):
    from server.tools.search import register

    captured = {}

    class _FakeMCP:
        def tool(self, *_args, **_kwargs):
            def decorator(fn):
                captured[fn.__name__] = fn
                return fn

            return decorator

    register(_FakeMCP(), engine)
    await engine.store("A searchable note about deploys", memory_type="episodic")

    text = await captured["search_memory"](query="deploys")

    assert "MemoryType." not in text
    assert "Type: episodic" in text


@pytest.mark.asyncio
async def test_session_tool_output_has_no_raw_enum_repr(engine):
    from server.tools.session import register

    captured = {}

    class _FakeMCP:
        def tool(self, *_args, **_kwargs):
            def decorator(fn):
                captured[fn.__name__] = fn
                return fn

            return decorator

    register(_FakeMCP(), engine)

    text = await captured["create_session"](name="enum-format-check")

    assert "SessionStatus." not in text
    assert "Status: active" in text
