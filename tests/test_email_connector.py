"""Tests for the Email (.mbox/.eml) connector — Phase 1 capture source #2.
All offline: raw RFC 822 strings via stdlib, no network, no credentials."""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.connectors import get_connector, list_connectors
from server.connectors.email_connector import EmailConnector, _plain_body
from server.core.memory_engine import MemoryEngine

# A simple plain-text message
EML_PLAIN = """From: Dana Acme <dana@acme.com>
To: You <you@example.com>
Cc: Bob <bob@example.com>
Subject: Pricing for Q3
Date: Mon, 20 Jan 2026 15:00:00 +0000
Message-ID: <msg-1@acme.com>
Content-Type: text/plain; charset="utf-8"

Hi, here is the pricing proposal for Q3.
We can offer 20% off if you commit by Friday.
"""

# A MIME multipart with html + plain, plus an encoded subject
EML_MULTIPART = """From: =?utf-8?q?No=2DReply?= <no-reply@notify.com>
To: you@example.com
Subject: =?utf-8?q?Your_receipt?=
Date: Tue, 21 Jan 2026 09:30:00 +0000
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="B"

--B
Content-Type: text/plain; charset="utf-8"

Receipt: order 123 total $9.
--B
Content-Type: text/html; charset="utf-8"

<html><body><p>Receipt: order 123 total $9.</p></body></html>
--B--
"""


def _write(text: str, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _write_mbox(*messages: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".mbox")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for m in messages:
            f.write("From MAILER-DAEMON " + "Mon Jan 20 15:00:00 2026\n")
            f.write(m)
            if not m.endswith("\n"):
                f.write("\n")
            f.write("\n")
    return path


# ── parser (unit) ──────────────────────────────────────────────────


def test_plain_body_strips_html_and_truncates():
    from email import message_from_string

    msg = message_from_string(EML_MULTIPART)
    body = _plain_body(msg, 600)
    assert "Receipt: order 123 total $9." in body
    assert "<p>" not in body  # html stripped (plain part preferred anyway)


@pytest.mark.asyncio
async def test_eml_single_fields():
    path = _write(EML_PLAIN, ".eml")
    try:
        conn = EmailConnector()
        await conn.connect({"eml_path": path})
        items = await conn.fetch()
        assert len(items) == 1
        m = items[0]
        assert "Pricing for Q3" in m["content"]
        assert "Dana Acme <dana@acme.com>" in m["content"]
        assert "20% off" in m["content"]
        assert m["tags"] == ["email"]
        assert m["metadata"]["from"] == "Dana Acme <dana@acme.com>"
        assert "You <you@example.com>" in m["metadata"]["to"]
        assert "Bob <bob@example.com>" in m["metadata"]["cc"]
        assert m["metadata"]["message_id"] == "<msg-1@acme.com>"
        assert m["metadata"]["captured_at"] == "2026-01-20T15:00:00+00:00"
        await conn.disconnect()
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_mbox_multiple_and_sorted_recent_first():
    path = _write_mbox(EML_PLAIN, EML_MULTIPART)
    try:
        conn = EmailConnector()
        await conn.connect({"mbox_path": path})
        items = await conn.fetch()
        assert len(items) == 2
        dates = [i["metadata"]["date"] for i in items]
        assert dates == sorted(dates, reverse=True)  # newest first
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_encoded_subject_decoded():
    path = _write(EML_MULTIPART, ".eml")
    try:
        conn = EmailConnector()
        await conn.connect({"eml_path": path})
        items = await conn.fetch()
        assert items[0]["metadata"]["subject"] == "Your receipt"
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_exclude_senders():
    path = _write_mbox(EML_PLAIN, EML_MULTIPART)
    try:
        conn = EmailConnector()
        await conn.connect({"mbox_path": path, "exclude_senders": ["no-reply"]})
        items = await conn.fetch()
        assert len(items) == 1
        assert "Pricing for Q3" in items[0]["content"]
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_past_days_window():
    old = datetime.now(timezone.utc) - timedelta(days=400)
    eml = (
        f"From: old@x.com\nTo: you@x.com\nSubject: Old news\n"
        f"Date: {format_datetime(old)}\n\nbody\n"
    )
    path = _write(eml, ".eml")
    try:
        conn = EmailConnector()
        await conn.connect({"eml_path": path, "past_days": 30})
        assert await conn.fetch() == []
        conn2 = EmailConnector()
        await conn2.connect({"eml_path": path})
        assert len(await conn2.fetch()) == 1
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_body_chars_zero_omits_body():
    path = _write(EML_PLAIN, ".eml")
    try:
        conn = EmailConnector()
        await conn.connect({"eml_path": path, "body_chars": 0})
        items = await conn.fetch()
        assert "Body:" not in items[0]["content"]
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_eml_dir():
    d = tempfile.mkdtemp()
    try:
        for i, m in enumerate((EML_PLAIN, EML_MULTIPART)):
            with open(os.path.join(d, f"{i}.eml"), "w", encoding="utf-8") as f:
                f.write(m)
        # a non-eml file must be ignored
        with open(os.path.join(d, "notes.txt"), "w") as f:
            f.write("ignore me")
        conn = EmailConnector()
        await conn.connect({"eml_dir": d})
        assert len(await conn.fetch()) == 2
    finally:
        import shutil

        shutil.rmtree(d)


@pytest.mark.asyncio
async def test_connect_errors():
    conn = EmailConnector()
    with pytest.raises(FileNotFoundError):
        await conn.connect({"mbox_path": "/nope/x.mbox"})
    with pytest.raises(ValueError):
        await conn.connect({})


# ── registry ───────────────────────────────────────────────────────


def test_email_in_registry():
    names = {c["name"] for c in list_connectors()}
    assert "email" in names
    assert "mbox" in get_connector("email").help_text().lower()


# ── end-to-end via API ─────────────────────────────────────────────


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
async def test_api_email_import_and_ask(api_client):
    path = _write_mbox(EML_PLAIN)
    try:
        r = await api_client.post(
            "/api/connectors/import",
            json={"connector": "email", "config": {"mbox_path": path}, "project": "work"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["stored"] == 1

        r = await api_client.get("/api/memories", params={"source": "connector:email"})
        mems = r.json()
        assert len(mems) == 1
        assert mems[0]["project"] == "work"

        r = await api_client.post("/api/ask", json={"question": "what did Dana say about pricing?"})
        assert r.status_code == 200
        assert r.json()["sources"]
    finally:
        os.unlink(path)
