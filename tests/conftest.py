"""Shared test setup.

The suite must describe its own environment. It did not, in two ways.

Several tests assert what happens with *no* LLM configuration but read the
developer's real environment, so on a machine with OPENAI_BASE_URL pointed at
OpenRouter and SUMMARY_MODE set they failed — while passing in CI and on a
clean checkout. That is the worst failure mode a test can have: red for a
reason that has nothing to do with the change under test, which trains
everyone to ignore it. Clearing those variables makes "unset" the baseline; a
test that wants a value still sets it with monkeypatch, which runs after this
fixture and wins.

The librarian watcher is the other one. It starts with the app and writes into
whatever store the app is using — a row no test asked for, arriving from a
background thread at an unpredictable moment. Tests that exercise the watcher
turn it on themselves.
"""

from __future__ import annotations

import os

import pytest

os.environ["LEVH_LIBRARIAN"] = "0"

# Read by server.core.llm_endpoint, llm_policy and summarizer. Anything here
# changes whether a call goes out, where it goes, and with which model.
_LLM_ENV = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "SUMMARY_MODE",
    "SUMMARY_MODEL",
)


@pytest.fixture(autouse=True)
def _neutral_llm_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _LLM_ENV:
        monkeypatch.delenv(name, raising=False)
