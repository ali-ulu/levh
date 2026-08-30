from __future__ import annotations

import json
import socket

import pytest

from server.core.onboarding import read_receipt, write_receipt


def test_receipt_contains_only_setup_metadata(tmp_path):
    path = tmp_path / "receipt.json"
    receipt = write_receipt(
        database_ready=True,
        first_memory_ready=True,
        mcp_client="claude",
        mcp_profile="work",
        demo_mode=False,
        dogfood_enabled=False,
        path=path,
        completed_at="2026-07-11T00:00:00+00:00",
    )
    raw = path.read_text(encoding="utf-8")
    assert read_receipt(path) == receipt
    for forbidden in (
        "memory_content",
        "query",
        "token",
        "api_key",
        "email",
        str(tmp_path),
    ):
        assert forbidden not in raw.lower()


def test_onboarding_helpers_make_no_network_calls(tmp_path, monkeypatch):
    def _boom(*args, **kwargs):  # pragma: no cover
        raise AssertionError("onboarding attempted network access")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    path = tmp_path / "receipt.json"
    write_receipt(
        database_ready=True,
        first_memory_ready=False,
        mcp_client="cursor",
        mcp_profile="minimal",
        demo_mode=False,
        dogfood_enabled=False,
        path=path,
    )
    assert json.loads(path.read_text(encoding="utf-8"))["mcp_profile"] == "minimal"
