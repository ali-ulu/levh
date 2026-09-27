"""The dashboard directory must carry its bundles, not just an index.html.

Regression guard for a clean-checkout failure: ``frontend/out`` is committed as
HTML shells while its content-hashed ``_next`` bundles are gitignored and kept
out of the release commits. ``_dashboard_dir`` accepted the first directory with
an ``index.html``, so on a fresh clone it chose that partial export and the
server answered ``/`` with 200 while every ``_next`` chunk 404'd — React could
not hydrate and the dashboard was blank. The packaged copy under
``server/dashboard`` was complete and never reached.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import _dashboard_dir


def _export(root, name, *, index=True, bundles=False, static=False):
    """Build a fake export directory and return its path."""
    path = root / name
    path.mkdir(parents=True)
    if index:
        (path / "index.html").write_text("<html></html>", encoding="utf-8")
    if bundles:
        # Next's client bundles live under `_next/static`; `_next` on its own
        # can exist without anything servable in it.
        target = path / "_next" / "static" if static else path / "_next"
        target.mkdir(parents=True)
    return str(path)


def test_a_complete_export_is_preferred_over_a_partial_one(tmp_path, monkeypatch):
    partial = _export(tmp_path, "partial", index=True, bundles=False)
    complete = _export(tmp_path, "complete", index=True, bundles=True, static=True)

    monkeypatch.setattr(
        "server.api._dashboard_candidates", lambda: [partial, complete]
    )

    assert _dashboard_dir() == complete


def test_a_directory_without_bundles_is_only_a_last_resort(tmp_path, monkeypatch):
    partial = _export(tmp_path, "partial", index=True, bundles=False)

    monkeypatch.setattr("server.api._dashboard_candidates", lambda: [partial])

    # Nothing better exists, so it is still returned rather than leaving the
    # mount unconfigured — a bundle-less export is better than no dashboard.
    assert _dashboard_dir() == partial


def test_a_directory_without_an_index_is_rejected(tmp_path, monkeypatch):
    no_index = _export(tmp_path, "no-index", index=False, bundles=True, static=True)

    monkeypatch.setattr("server.api._dashboard_candidates", lambda: [no_index])

    assert _dashboard_dir() is None


def test_the_override_wins_when_it_is_complete(tmp_path, monkeypatch):
    override = _export(tmp_path, "override", index=True, bundles=True, static=True)
    complete = _export(tmp_path, "complete", index=True, bundles=True, static=True)

    monkeypatch.setattr(
        "server.api._dashboard_candidates", lambda: [override, complete]
    )

    assert _dashboard_dir() == override


def test_the_override_does_not_win_when_it_is_partial(tmp_path, monkeypatch):
    override = _export(tmp_path, "override", index=True, bundles=False)
    complete = _export(tmp_path, "complete", index=True, bundles=True, static=True)

    monkeypatch.setattr(
        "server.api._dashboard_candidates", lambda: [override, complete]
    )

    assert _dashboard_dir() == complete


def test_missing_directories_are_skipped(tmp_path, monkeypatch):
    complete = _export(tmp_path, "complete", index=True, bundles=True, static=True)
    missing = str(tmp_path / "does-not-exist")

    monkeypatch.setattr(
        "server.api._dashboard_candidates", lambda: ["", missing, complete]
    )

    assert _dashboard_dir() == complete
