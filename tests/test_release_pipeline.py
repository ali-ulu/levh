"""Tests for the deterministic release pipeline (scripts/release.py).

These exercise the pure, side-effect-light stages — version bumping and the
consistency assertion — against a temp copy of the real version-bearing files,
without invoking npm or `python -m build`. The point is to guarantee the bump
touches every canonical site exactly once and that `assert_consistent`
actually catches the drift class it was written to prevent (packaged dashboard
lagging the source, the 2.22.0 regression).
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest

release = importlib.import_module("scripts.release")


def test_all_version_sites_bump_exactly_once():
    """Every canonical site matches the bump regex exactly once — no site is
    silently skipped (count==0) or double-matched (count>1)."""
    for path, pattern, kind in release.VERSION_SITES:
        text = release._read(path)
        target = "9.9.9" if kind == release.FULL else "9.9"
        _new, count = pattern.subn(
            lambda m: f"{m.group(1)}{target}{m.group(3)}", text
        )
        assert count == 1, f"{path} matched {count} times (want 1)"


def test_minor_helper():
    assert release._minor("2.23.0") == "2.23"
    assert release._minor("2.23.0-A") == "2.23"
    assert release._minor("10.4.7") == "10.4"


def test_version_regex_accepts_and_rejects():
    assert release.VERSION_RE.match("2.23.0")
    assert release.VERSION_RE.match("2.23.0-rc.1")
    assert not release.VERSION_RE.match("2.23")
    assert not release.VERSION_RE.match("v2.23.0")
    assert not release.VERSION_RE.match("2.23.0 ")


def test_check_mode_passes_on_current_tree():
    """The shipped tree must be internally consistent — this is the same gate
    `python scripts/release.py --check` runs."""
    release.assert_consistent()  # raises ReleaseError on any drift


def _write_min_tree(root, version, minor, dashboard_badge):
    """Lay down a minimal set of version-bearing files under a fake repo root."""
    (root / "server").mkdir(parents=True, exist_ok=True)
    (root / "frontend" / "src" / "components" / "layout").mkdir(
        parents=True, exist_ok=True
    )
    (root / "server" / "dashboard").mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(f'version = "{version}"\n', encoding="utf-8")
    (root / "frontend" / "package.json").write_text(
        f'{{\n  "version": "{version}"\n}}\n', encoding="utf-8"
    )
    (root / "frontend" / "package-lock.json").write_text(
        json.dumps({
            "name": "levh-frontend",
            "version": version,
            "lockfileVersion": 3,
            "packages": {"": {"name": "levh-frontend", "version": version}},
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "server" / "api.py").write_text(
        f'app = FastAPI(version="{version}")\n', encoding="utf-8"
    )
    (root / "frontend" / "src" / "components" / "layout" / "sidebar.tsx").write_text(
        f"LEVH Engine v{minor}\n", encoding="utf-8"
    )
    (root / "server" / "dashboard" / "index.html").write_text(
        f"<footer>LEVH Engine v{dashboard_badge}</footer>", encoding="utf-8"
    )


def test_assert_catches_stale_packaged_dashboard(tmp_path, monkeypatch):
    """The regression that motivated the script: source at X.Y but the packaged
    dashboard still shows the previous minor."""
    monkeypatch.setattr(release, "REPO_ROOT", tmp_path)
    _write_min_tree(tmp_path, version="2.23.0", minor="2.23", dashboard_badge="2.22")
    with pytest.raises(release.ReleaseError) as exc:
        release.assert_consistent()
    assert "packaged" in str(exc.value)
    assert "2.22" in str(exc.value)


def test_assert_passes_when_everything_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "REPO_ROOT", tmp_path)
    _write_min_tree(tmp_path, version="2.23.0", minor="2.23", dashboard_badge="2.23")
    release.assert_consistent()  # no raise


def test_bump_rewrites_temp_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "REPO_ROOT", tmp_path)
    _write_min_tree(tmp_path, version="2.22.1", minor="2.22", dashboard_badge="2.22")
    release.bump("2.23.0")
    assert 'version = "2.23.0"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert '"version": "2.23.0"' in (tmp_path / "frontend" / "package.json").read_text(encoding="utf-8")
    assert 'version="2.23.0"' in (tmp_path / "server" / "api.py").read_text(encoding="utf-8")
    lock = json.loads((tmp_path / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["version"] == "2.23.0"
    assert lock["packages"][""]["version"] == "2.23.0"
    sidebar = (
        tmp_path / "frontend" / "src" / "components" / "layout" / "sidebar.tsx"
    ).read_text(encoding="utf-8")
    assert "LEVH Engine v2.23" in sidebar
    assert "v2.22" not in sidebar


def test_bump_raises_when_site_pattern_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "REPO_ROOT", tmp_path)
    _write_min_tree(tmp_path, version="2.22.1", minor="2.22", dashboard_badge="2.22")
    # Corrupt one site so its pattern no longer matches.
    (tmp_path / "server" / "api.py").write_text("app = FastAPI()\n", encoding="utf-8")
    with pytest.raises(release.ReleaseError) as exc:
        release.bump("2.23.0")
    assert "server/api.py" in str(exc.value)


def test_release_excludes_build_and_egg_info_artifacts():
    assert release._is_excluded(Path("build/lib/server/api.py"))
    assert release._is_excluded(Path("levh.egg-info/PKG-INFO"))
    assert release._is_excluded(Path("other-package.egg-info/PKG-INFO"))
    assert not release._is_excluded(Path("server/dashboard/index.html"))


def test_frontend_lock_supports_react_19_without_legacy_peer_bypass():
    root = Path(__file__).resolve().parents[1]
    lock = json.loads((root / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    for package in ("node_modules/lucide-react", "node_modules/next-themes"):
        peer = lock["packages"][package].get("peerDependencies", {})
        assert "19" in peer.get("react", ""), f"{package} does not advertise React 19 support"

    for path in (
        root / "Dockerfile",
        root / ".github" / "workflows" / "ci.yml",
        root / "scripts" / "release.py",
        root / "README.md",
    ):
            assert "legacy-peer-deps" not in path.read_text(encoding="utf-8"), f"legacy peer bypass remains in {path}"


def test_frontend_runtime_versions_are_pinned_to_supported_major():
    root = Path(__file__).resolve().parents[1]
    package = json.loads((root / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert package["engines"]["node"] == ">=20 <23"
    assert package["engines"]["npm"] == ">=10 <11"
    assert package["packageManager"].startswith("npm@10.")
    assert (root / ".nvmrc").read_text(encoding="utf-8").strip() == "22"
