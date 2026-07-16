#!/usr/bin/env python3
"""Deterministic StackMemory release pipeline.

One command takes the repo from a source tree to a verified, version-consistent
distributable. The ordering is the whole point: the frontend is built *after*
the version bump so the packaged dashboard can never lag the source (the
2.22.0 "packaged dashboard showed v2.21" drift that motivated this script).

Pipeline stages (in order):

    1. bump      — rewrite the version string in all four canonical locations
    2. build     — clean `next build` static export into frontend/out
    3. sync      — replace server/dashboard/ with the fresh frontend/out/
    4. assert    — fail loudly if any version string disagrees (source + packaged)
    5. wheel     — `python -m build --wheel` + `twine check`
    6. zip       — package the source tree into dist/stackmemory-<version>.zip

Usage:
    python scripts/release.py --version 2.23.0        # full release
    python scripts/release.py --check                 # verify consistency only
    python scripts/release.py --version 2.23.0 --skip-wheel --skip-zip

The script is idempotent: re-running with the same --version is a no-op bump
followed by a clean rebuild. It never runs git.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# --- Canonical version locations ---------------------------------------------
# Each entry: (relative path, compiled regex with one capture group around the
# version substring, how to render the replacement given the target version).
# Three files carry the full X.Y.Z; the sidebar badge shows only the X.Y minor.
FULL = "full"
MINOR = "minor"

VERSION_SITES = [
    ("pyproject.toml", re.compile(r'^(version = ")([^"]+)(")', re.MULTILINE), FULL),
    ("frontend/package.json", re.compile(r'^(  "version": ")([^"]+)(")', re.MULTILINE), FULL),
    ("server/api.py", re.compile(r'(version=")([^"]+)(")'), FULL),
    (
        "frontend/src/components/layout/sidebar.tsx",
        re.compile(r"(Memory Engine v)(\d+\.\d+)()"),
        MINOR,
    ),
]

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?$")


class ReleaseError(RuntimeError):
    """A stage failed; message is printed and the process exits non-zero."""


# --- small helpers -----------------------------------------------------------
def _minor(version: str) -> str:
    major, minor, *_ = version.split(".")
    return f"{major}.{minor}"


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    (REPO_ROOT / path).write_text(text, encoding="utf-8")


def _pyproject_version() -> str:
    m = re.search(r'^version = "([^"]+)"', _read("pyproject.toml"), re.MULTILINE)
    if not m:
        raise ReleaseError("could not read version from pyproject.toml")
    return m.group(1)


def _step(n: int, total: int, label: str) -> None:
    print(f"\n[{n}/{total}] {label}", flush=True)


def _run(
    cmd: list[str],
    cwd: Path,
    stage: str,
    *,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> None:
    print(f"    $ {' '.join(cmd)}  (cwd={cwd.relative_to(REPO_ROOT) if cwd != REPO_ROOT else '.'})")
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    try:
        result = subprocess.run(cmd, cwd=cwd, env=process_env, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ReleaseError(f"{stage} timed out after {timeout}s: `{' '.join(cmd)}`") from exc
    if result.returncode != 0:
        raise ReleaseError(f"{stage} failed: `{' '.join(cmd)}` exited {result.returncode}")


# --- stages ------------------------------------------------------------------
def bump(version: str) -> None:
    """Rewrite every canonical version site to `version` (or its minor)."""
    minor = _minor(version)
    for path, pattern, kind in VERSION_SITES:
        target = version if kind == FULL else minor
        text = _read(path)
        new, count = pattern.subn(lambda m: f"{m.group(1)}{target}{m.group(3)}", text)
        if count == 0:
            raise ReleaseError(f"version pattern not found in {path} — file drifted?")
        if count > 1 and kind == FULL:
            # api.py legitimately has one FastAPI version=; more than one full
            # match means the regex is too greedy for this file.
            raise ReleaseError(f"version pattern matched {count} times in {path} (expected 1)")
        if new != text:
            _write(path, new)
            print(f"    bumped {path} -> {target}")
        else:
            print(f"    {path} already at {target}")

    # npm lockfiles duplicate the package version at the document root and in
    # packages[""]. Keep both synchronized without rewriting dependency data.
    lock_path = REPO_ROOT / "frontend" / "package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["version"] = version
    root_package = lock.setdefault("packages", {}).setdefault("", {})
    root_package["version"] = version
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    print(f"    bumped frontend/package-lock.json -> {version}")


def build_frontend(skip: bool) -> None:
    if skip:
        print("    (skipped --skip-build)")
        return
    frontend = REPO_ROOT / "frontend"
    out = frontend / "out"
    if out.exists():
        shutil.rmtree(out)
        print("    removed stale frontend/out")
    # Always start from the lockfile contract. `npm ci` removes any warmed
    # node_modules state, so release, CI and Docker validate the same graph.
    _run(["npm", "ci"], frontend, "frontend install", timeout=300)
    _run(
        ["npm", "run", "build"],
        frontend,
        "frontend build",
        env={"NEXT_TELEMETRY_DISABLED": "1"},
        timeout=600,
    )
    if not (out / "index.html").exists():
        raise ReleaseError("frontend build did not produce out/index.html")


def sync_dashboard(skip_build: bool) -> None:
    src = REPO_ROOT / "frontend" / "out"
    dst = REPO_ROOT / "server" / "dashboard"
    if not src.exists():
        if skip_build:
            print("    (no fresh frontend/out; --skip-build set, leaving server/dashboard as-is)")
            return
        raise ReleaseError("frontend/out missing — build stage did not run")
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    print(f"    synced frontend/out -> server/dashboard ({sum(1 for _ in dst.rglob('*') if _.is_file())} files)")


def assert_consistent() -> None:
    """Fail if any version site disagrees with pyproject, or the packaged
    dashboard still carries a stale 'Memory Engine vX.Y' badge."""
    version = _pyproject_version()
    minor = _minor(version)
    problems: list[str] = []

    # source sites
    if f'version="{version}"' not in _read("server/api.py"):
        problems.append(f"server/api.py not at version={version}")
    pkg = re.search(r'"version": "([^"]+)"', _read("frontend/package.json"))
    if not pkg or pkg.group(1) != version:
        problems.append(f"frontend/package.json = {pkg.group(1) if pkg else '?'} (want {version})")
    lock = json.loads(_read("frontend/package-lock.json"))
    lock_root = lock.get("packages", {}).get("", {}).get("version")
    if lock.get("version") != version or lock_root != version:
        problems.append(
            "frontend/package-lock.json versions "
            f"= {lock.get('version')}/{lock_root} (want {version}/{version})"
        )
    if f"Memory Engine v{minor}" not in _read("frontend/src/components/layout/sidebar.tsx"):
        problems.append(f"sidebar.tsx not at minor v{minor}")

    # packaged dashboard — scan every emitted asset for a stale badge
    dashboard = REPO_ROOT / "server" / "dashboard"
    badge = re.compile(r"Memory Engine v(\d+\.\d+)")
    if dashboard.exists():
        for path in dashboard.rglob("*"):
            if not path.is_file() or path.suffix not in {".html", ".js", ".txt"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, ValueError):
                continue
            for found in badge.findall(text):
                if found != minor:
                    problems.append(f"packaged {path.relative_to(dashboard)} shows v{found} (want v{minor})")
    else:
        problems.append("server/dashboard missing — nothing packaged")

    if problems:
        raise ReleaseError("version inconsistency:\n      - " + "\n      - ".join(problems))
    print(f"    all version sites consistent at {version} (badge v{minor})")


def build_wheel(skip: bool) -> None:
    if skip:
        print("    (skipped --skip-wheel)")
        return
    dist = REPO_ROOT / "dist"
    # Setuptools may reuse build/lib and silently retain removed or stale
    # dashboard assets. Always rebuild the wheel from a clean staging tree.
    for stale in (REPO_ROOT / "build", REPO_ROOT / "stackmemory.egg-info"):
        if stale.exists():
            shutil.rmtree(stale)
            print(f"    removed stale {stale.relative_to(REPO_ROOT)}")
    # Clear old wheels/sdists so `twine check dist/*` only sees this build.
    if dist.exists():
        for f in dist.glob("*.whl"):
            f.unlink()
        for f in dist.glob("*.tar.gz"):
            f.unlink()
    _run([sys.executable, "-m", "build", "--wheel"], REPO_ROOT, "wheel build")
    wheels = list(dist.glob("*.whl"))
    if not wheels:
        raise ReleaseError("wheel build produced no .whl in dist/")
    _run([sys.executable, "-m", "twine", "check", *[str(w) for w in wheels]], REPO_ROOT, "twine check")


def make_zip(version: str, skip: bool) -> Path | None:
    if skip:
        print("    (skipped --skip-zip)")
        return None
    dist = REPO_ROOT / "dist"
    dist.mkdir(exist_ok=True)
    zip_path = dist / f"stackmemory-{version}.zip"
    if zip_path.exists():
        zip_path.unlink()

    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(REPO_ROOT.rglob("*")):
            rel = path.relative_to(REPO_ROOT)
            if path.is_dir() or _is_excluded(rel):
                continue
            zf.write(path, Path("stackmemory-new") / rel)
            count += 1
    _verify_zip_clean(zip_path)
    print(f"    wrote {zip_path.relative_to(REPO_ROOT)} ({count} files, {zip_path.stat().st_size // 1024} KB)")
    return zip_path


# Exclude build/vcs cruft and local runtime state, but KEEP server/dashboard
# (the packaged UI) and template files like .env.example.
_EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".next", "out", ".pytest_cache",
    ".venv", "venv", "dist", "build", "stackmemory.egg-info",
    ".mypy_cache", ".ruff_cache", ".turbo", "logs",
}
_EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".tsbuildinfo", ".log", ".db", ".db-wal", ".db-shm"}
# Exact basenames to drop (local secrets / state). .env.example is kept.
_EXCLUDE_NAMES = {".env", ".env.local", ".DS_Store"}


def _is_excluded(rel: Path) -> bool:
    if any(part in _EXCLUDE_DIRS or part.endswith(".egg-info") for part in rel.parts):
        return True
    if rel.name in _EXCLUDE_NAMES:
        return True
    if rel.suffix in _EXCLUDE_SUFFIXES:
        return True
    return False


def _verify_zip_clean(zip_path: Path) -> None:
    """Fail the release if any forbidden artifact slipped into the zip — a
    packaged secret, local db, or build cache is a release-integrity bug."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    offenders = [
        n for n in names
        if _is_excluded(Path(n).relative_to("stackmemory-new"))
        or n.endswith("/stackmemory.db")
    ]
    if offenders:
        raise ReleaseError(
            "forbidden artifacts packaged in zip: " + ", ".join(sorted(offenders)[:10])
        )


# --- orchestration -----------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="StackMemory release pipeline")
    parser.add_argument("--version", help="target version X.Y.Z (bump + full pipeline)")
    parser.add_argument("--check", action="store_true", help="verify version consistency only")
    parser.add_argument("--skip-build", action="store_true", help="reuse existing frontend/out")
    parser.add_argument("--skip-wheel", action="store_true", help="skip wheel build + twine check")
    parser.add_argument("--skip-zip", action="store_true", help="skip source zip")
    args = parser.parse_args(argv)

    try:
        if args.check:
            print("Verifying version consistency…")
            assert_consistent()
            print("\nOK — versions consistent.")
            return 0

        if not args.version:
            parser.error("either --version X.Y.Z or --check is required")
        if not VERSION_RE.match(args.version):
            parser.error(f"--version {args.version!r} is not a valid X.Y.Z version")

        total = 6
        print(f"Releasing StackMemory {args.version}")
        _step(1, total, "Bump version strings")
        bump(args.version)
        _step(2, total, "Clean frontend build")
        build_frontend(args.skip_build)
        _step(3, total, "Sync dashboard into server/dashboard")
        sync_dashboard(args.skip_build)
        _step(4, total, "Assert version consistency")
        assert_consistent()
        _step(5, total, "Build wheel + twine check")
        build_wheel(args.skip_wheel)
        _step(6, total, "Package source zip")
        zip_path = make_zip(args.version, args.skip_zip)

        print(f"\nDone. StackMemory {args.version} is release-ready.")
        if zip_path:
            print(f"  zip: {zip_path}")
        return 0
    except ReleaseError as exc:
        print(f"\nRELEASE FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
