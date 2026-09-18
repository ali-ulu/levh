"""Guards for the repository-process files (issue #153).

A process file that is absent, empty, or malformed is worse than one that was
never added: GitHub silently ignores a broken issue form, and a ruff pin that
drifts from CI's lets a commit pass locally and fail in CI. These checks are
cheap and catch exactly that. They live in the suite so the files cannot be
dropped again without a red test — the same guard the docs tests provide for
references between `docs/` pages.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
GITHUB = ROOT / ".github"

REQUIRED_FILES = [
    GITHUB / "CODEOWNERS",
    GITHUB / "pull_request_template.md",
    GITHUB / "ISSUE_TEMPLATE" / "bug_report.yml",
    GITHUB / "ISSUE_TEMPLATE" / "feature_request.yml",
    ROOT / "CODE_OF_CONDUCT.md",
    ROOT / ".editorconfig",
    ROOT / ".pre-commit-config.yaml",
]


@pytest.mark.parametrize("path", REQUIRED_FILES, ids=lambda p: p.name)
def test_required_process_file_exists_and_is_not_a_stub(path: Path):
    assert path.is_file(), f"missing process file: {path.relative_to(ROOT)}"
    text = path.read_text(encoding="utf-8").strip()
    assert len(text.splitlines()) >= 5, (
        f"{path.relative_to(ROOT)} is a stub; a placeholder template or "
        "ownership file does not do the job it was added for"
    )


@pytest.mark.parametrize(
    "path",
    [GITHUB / "ISSUE_TEMPLATE" / "bug_report.yml", GITHUB / "ISSUE_TEMPLATE" / "feature_request.yml"],
    ids=["bug_report", "feature_request"],
)
def test_issue_forms_have_the_keys_github_requires(path: Path):
    """GitHub renders an issue form only when it is valid YAML with the
    documented top-level keys; a form missing `name`/`description`/`body`
    appears in the chooser with a generic fallback and the questions are lost.
    """
    form = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(form, dict), f"{path.name} is not a mapping"
    for key in ("name", "description", "body"):
        assert key in form, f"{path.name} is missing top-level `{key}`"
    assert isinstance(form["body"], list) and form["body"], f"{path.name} has an empty body"
    for block in form["body"]:
        assert isinstance(block, dict) and "type" in block, f"{path.name}: bad body block {block!r}"
        assert "attributes" in block, f"{path.name}: block without attributes {block!r}"


_OWNER_LINE_RE = re.compile(r"^\S+\s+(@[\w.-]+(?:\s+@[\w.-]+)*)\s*$")


def test_codeowners_grammar_is_valid_and_covers_the_high_risk_paths():
    """Every non-comment line must be `pattern @owner...`. A line without an
    owner (or with a typo'd `@`) is accepted by the file parser and then
    assigned to nobody, which is the failure this test exists to catch.
    """
    lines = (GITHUB / "CODEOWNERS").read_text(encoding="utf-8").splitlines()
    owned: list[str] = []
    for lineno, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        assert _OWNER_LINE_RE.match(line), f"CODEOWNERS:{lineno} is not `pattern @owner`: {line!r}"
        owned.append(line.split()[0])

    for required in ("/server/core/engine/", "/.github/workflows/"):
        assert required in owned, f"{required} has no owner in CODEOWNERS"


def _pre_commit_hooks() -> dict[str, dict]:
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    return {hook["id"]: hook for repo in config["repos"] for hook in repo["hooks"]}


# Tracked build output. `frontend/out/` is a Next.js static export and
# `server/dashboard/` its packaged copy; both are rewritten wholesale by the
# next build, so normalizing their whitespace produces churn nobody asked for.
BUILD_OUTPUT_PATHS = [
    "frontend/out/404.html",
    "frontend/out/_next/static/chunks/100-d7253503f946971e.js",
    "server/dashboard/index.html",
    "server/dashboard/_next/static/chunks/100-d7253503f946971e.js",
]
SOURCE_PATHS = [
    "server/api.py",
    "frontend/src/app/agents/page.tsx",
    "tests/test_repo_process_files.py",
    ".pre-commit-config.yaml",
]


@pytest.mark.parametrize("hook_id", ["end-of-file-fixer", "trailing-whitespace"])
def test_generic_hooks_skip_checked_in_build_output(hook_id: str):
    """`end-of-file-fixer` and `trailing-whitespace` have no language scope, so
    without an `exclude` they rewrite every tracked file — minified bundles and
    generated HTML included. That turns a developer's first commit after
    `pre-commit install` into a hundreds-of-lines diff of build output that the
    next build regenerates anyway (issue #175). This asserts the hooks keep
    skipping those directories while still covering real sources, so a lost or
    broadened `exclude` fails loudly instead of silently churning the tree.
    """
    hook = _pre_commit_hooks().get(hook_id)
    assert hook is not None, f"pre-commit no longer runs {hook_id}"
    pattern = hook.get("exclude")
    assert pattern, f"{hook_id} has no `exclude`; it will rewrite build output"

    compiled = re.compile(pattern)
    for path in BUILD_OUTPUT_PATHS:
        assert compiled.search(path), f"{hook_id} would rewrite build output: {path}"
    for path in SOURCE_PATHS:
        assert not compiled.search(path), f"{hook_id} unexpectedly skips source: {path}"


def test_ruff_hook_is_scoped_to_python():
    """The config's comment promises ruff never touches the frontend. Ruff's
    `extend-exclude` only covers `ruff check .`; the hook needs its own scope so
    a stray non-Python file cannot be linted (and the comment cannot drift from
    the config again — issue #175).
    """
    hook = _pre_commit_hooks().get("ruff")
    assert hook is not None, "pre-commit no longer runs ruff"
    scope = hook.get("types") or hook.get("files")
    assert scope, "the ruff hook has no language/`files` scope"
    assert hook.get("types") == ["python"], f"ruff hook scope is not Python-only: {scope!r}"


def test_pre_commit_pins_the_same_ruff_as_ci():
    """Local and CI lint must agree. When the pin drifts, a rule added by a
    newer ruff turns green locally (older ruff) and red in CI, or the reverse —
    the contributor then "fixes" a codebase that was never the problem.
    """
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    pinned = {
        hook["id"]: repo["rev"]
        for repo in config["repos"]
        for hook in repo["hooks"]
    }
    assert "ruff" in pinned, "pre-commit no longer runs ruff"

    local = pinned["ruff"]
    ci = (GITHUB / "workflows" / "ci.yml").read_text(encoding="utf-8")
    installed = re.search(r'ruff==(\d+\.\d+\.\d+)', ci)
    assert installed, "ci.yml no longer pins an exact ruff version"
    # pre-commit's ruff hook tags carry a leading `v`; the tool version does not.
    assert local.lstrip("v") == installed.group(1), (
        f"pre-commit pins ruff {local} but CI installs {installed.group(1)}"
    )


def test_editorconfig_declares_root_and_line_endings():
    text = (ROOT / ".editorconfig").read_text(encoding="utf-8")
    assert re.search(r"^root\s*=\s*true$", text, re.MULTILINE), ".editorconfig is not root"
    section = re.search(r"^\[\*\]\n(.*?)(?=^\[|\Z)", text, re.MULTILINE | re.DOTALL)
    assert section, ".editorconfig has no `[*]` section"
    assert "end_of_line = lf" in section.group(1), ".editorconfig does not normalize line endings"


def test_coverage_artifacts_are_gitignored_when_ci_measures_coverage():
    """CI runs pytest with `--cov=server` (issue #176), and coverage.py writes
    `.coverage` into the working directory. An unignored data file shows up as
    untracked noise in every developer's `git status` and is one `git add -A`
    away from being committed, so the ignore rules must follow the CI flag
    (issue #197). The same check covers the `--cov-report=html` output.
    """
    ci = (GITHUB / "workflows" / "ci.yml").read_text(encoding="utf-8")
    if "--cov" not in ci:
        pytest.skip("ci.yml no longer measures coverage")

    for artifact in (".coverage", ".coverage.hostname.12345", "htmlcov/index.html"):
        result = subprocess.run(
            ["git", "check-ignore", "-q", artifact],
            cwd=ROOT,
            capture_output=True,
        )
        assert result.returncode == 0, (
            f"{artifact} is not ignored although CI measures coverage"
        )


def _mypy_config() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["mypy"]


def test_mypy_is_a_ci_gate_and_is_pinned_like_ruff():
    """Issue #195: `#147`'s type-check item was deferred and nearly lost. The
    gate is only real if CI actually runs it, so assert the step exists and that
    the mypy version installed in CI is the version pinned in the dev extra — a
    drift there is the same trap `test_pre_commit_pins_the_same_ruff_as_ci`
    guards against.
    """
    ci = (GITHUB / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python -m mypy" in ci, "ci.yml no longer runs mypy"

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev_extra = project["project"]["optional-dependencies"]["dev"]
    pinned = next((re.search(r'mypy==(\d+\.\d+\.\d+)', req) for req in dev_extra if "mypy" in req), None)
    assert pinned, "the dev extra no longer pins an exact mypy version"

    # The step installs from the dev extra, so CI and the extra cannot drift;
    # this asserts the install actually comes from `.[dev]`.
    assert 'pip install -e ".[dev]"' in ci, "ci.yml no longer installs the dev extra"


def test_mypy_file_list_is_nonempty_and_only_lists_existing_modules():
    """The tier is a ratchet (issue #195): it starts at the modules that are
    clean today and grows. An entry that no longer exists would make mypy skip
    the check silently, and an empty list would turn the gate into a no-op.
    """
    files = _mypy_config().get("files")
    assert files, "mypy `files` is empty; the type gate checks nothing"
    for entry in files:
        assert (ROOT / entry).is_file(), f"mypy `files` names a missing module: {entry}"

