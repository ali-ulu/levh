"""Packaging regression tests for public install readiness."""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    assert match, "could not find version in pyproject.toml"
    return match.group(1)


def _expected_minor_version() -> str:
    version = _pyproject_version()
    major, minor, *_ = version.split(".")
    return f"{major}.{minor}"




def test_frontend_package_versions_match_pyproject():
    version = _pyproject_version()
    package = json.loads((REPO_ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    lock = json.loads((REPO_ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    assert package["version"] == version
    assert lock["version"] == version
    assert lock["packages"][""]["version"] == version


def test_source_sidebar_version_matches_pyproject():
    expected = _expected_minor_version()
    sidebar = (REPO_ROOT / "frontend/src/components/layout/sidebar.tsx").read_text(
        encoding="utf-8"
    )
    assert f"Memory Engine v{expected}" in sidebar


def test_packaged_dashboard_version_matches_pyproject():
    dashboard_dir = REPO_ROOT / "server" / "dashboard"
    if not dashboard_dir.exists():
        import pytest

        pytest.skip("no packaged dashboard")

    expected = _expected_minor_version()
    pattern = re.compile(r"Memory Engine v(\d+\.\d+)")
    stale = []
    for glob_pattern in ("**/*.txt", "**/*.html", "**/*.js"):
        for path in dashboard_dir.glob(glob_pattern):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, ValueError):
                continue
            for found in pattern.findall(text):
                if found != expected:
                    stale.append((str(path.relative_to(dashboard_dir)), found))

    assert not stale, (
        f"packaged dashboard has stale version strings (expected v{expected}): {stale}"
    )


def test_api_version_matches_pyproject():
    version = _pyproject_version()
    api_source = (REPO_ROOT / "server" / "api.py").read_text(encoding="utf-8")
    assert f'version="{version}"' in api_source


def test_cli_entrypoint_imports():
    mod = importlib.import_module("server.cli")
    assert callable(mod.main)


def test_packaged_benchmark_imports_from_server_core():
    mod = importlib.import_module("server.core.benchmark")
    assert callable(mod.run_benchmark)


def test_dashboard_static_export_packaged():
    import server

    dashboard_dir = Path(server.__file__).with_name("dashboard")
    assert (dashboard_dir / "index.html").exists()


def test_public_docs_do_not_claim_huqan_bridge():
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "HUQAN" not in readme


def test_packaged_evaluation_fixtures_are_available():
    from server.core.evaluation import DEFAULT_FIXTURE_DIR, load_fixtures

    assert DEFAULT_FIXTURE_DIR.is_dir()
    assert len(load_fixtures()) == 9
