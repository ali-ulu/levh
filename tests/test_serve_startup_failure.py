"""A failed startup must exit non-zero instead of hanging (regression).

``levh serve`` used to keep the process alive after the lifespan raised: the DB
connection was already open, its aiosqlite worker thread is not a daemon, and
interpreter shutdown waited on that thread forever. The operator saw a process
with no listener and no exit - the "LevH did not start" symptom, silent.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_serve_exits_non_zero_when_startup_fails(tmp_path):
    # A file that is not a SQLite database: connecting is fine, the first
    # statement fails - after the engine holds an open connection.
    store = tmp_path / "not-a-database.db"
    store.write_bytes(os.urandom(4096))

    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("LEVH_", "STACKMEMORY_"))
    }
    env["SQLITE_DB_PATH"] = str(store)
    env["EMBEDDER_MODE"] = "hash"
    env["LEVH_LIBRARIAN"] = "0"

    # timeout is the regression signal: the pre-fix process never exited.
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "server.cli",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(_free_port()),
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert "Application startup failed" in completed.stderr, completed.stderr
