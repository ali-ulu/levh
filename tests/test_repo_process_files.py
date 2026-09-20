"""Guards for the repository-process files (issue #153).

A process file that is absent, empty, or malformed is worse than one that was
never added: GitHub silently ignores a broken issue form, and a ruff pin that
drifts from CI's lets a commit pass locally and fail in CI. These checks are
cheap and catch exactly that. They live in the suite so the files cannot be
dropped again without a red test — the same guard the docs tests provide for
references between `docs/` pages.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
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
    # CI installs ruff through the lockfile (issue #146), so the pin lives in
    # the dev extra rather than as a `pip install ruff==x.y.z` line in ci.yml.
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev_extra = project["project"]["optional-dependencies"]["dev"]
    installed = next((re.search(r'ruff==(\d+\.\d+\.\d+)', req) for req in dev_extra if "ruff" in req), None)
    assert installed, "the dev extra no longer pins an exact ruff version"
    # pre-commit's ruff hook tags carry a leading `v`; the tool version does not.
    assert local.lstrip("v") == installed.group(1), (
        f"pre-commit pins ruff {local} but the dev extra installs {installed.group(1)}"
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


def test_pip_audit_resolved_requirements_is_gitignored_when_ci_writes_it():
    """CI's `pip-audit` job freezes the environment into
    `resolved-requirements.txt` in the workspace (#223). Left unignored, that
    file shows up as untracked noise in every developer's `git status` and is
    one `git add -A` away from being committed. The ignore rule must follow the
    CI step, the same way the coverage rule follows `--cov` (#197).
    """
    ci = (GITHUB / "workflows" / "ci.yml").read_text(encoding="utf-8")
    if "resolved-requirements.txt" not in ci:
        pytest.skip("ci.yml no longer writes the pip-audit resolved graph")

    result = subprocess.run(
        ["git", "check-ignore", "-q", "resolved-requirements.txt"],
        cwd=ROOT,
        capture_output=True,
    )
    assert result.returncode == 0, (
        "resolved-requirements.txt is not ignored although CI writes it"
    )


def test_sbom_files_are_gitignored_when_ci_writes_them():
    """CI's `sbom` job writes two CycloneDX BOMs: `dist/sbom-python.json` (the
    `dist/` rule happens to cover it) and `frontend/sbom-frontend.json`, whose
    path is not covered by any build-output rule (#242). Left unignored, the
    frontend BOM shows up as untracked noise in every developer's `git status`
    and is one `git add -A` away from committing a machine-generated JSON.
    """
    ci = (GITHUB / "workflows" / "ci.yml").read_text(encoding="utf-8")
    artifacts = ["dist/sbom-python.json", "frontend/sbom-frontend.json"]
    if not all(artifact in ci for artifact in artifacts):
        pytest.skip("ci.yml no longer writes the SBOM files")

    for artifact in artifacts:
        result = subprocess.run(
            ["git", "check-ignore", "-q", artifact],
            cwd=ROOT,
            capture_output=True,
        )
        assert result.returncode == 0, (
            f"{artifact} is not ignored although CI writes it"
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
    assert "uv run --frozen mypy" in ci, "ci.yml no longer runs mypy"

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev_extra = project["project"]["optional-dependencies"]["dev"]
    pinned = next((re.search(r'mypy==(\d+\.\d+\.\d+)', req) for req in dev_extra if "mypy" in req), None)
    assert pinned, "the dev extra no longer pins an exact mypy version"

    # The step installs from the locked dev extra, so CI and the extra cannot
    # drift; this asserts the install actually comes from `.[dev]`. Since
    # issue #146 the install goes through the lockfile (`uv sync --frozen
    # --extra dev`) instead of a bare `pip install -e ".[dev]"`.
    assert "uv sync --frozen --extra dev" in ci, "ci.yml no longer installs the dev extra"


def test_mypy_file_list_is_nonempty_and_only_lists_existing_modules():
    """The tier is a ratchet (issue #195): it starts at the modules that are
    clean today and grows. An entry that no longer exists would make mypy skip
    the check silently, and an empty list would turn the gate into a no-op.
    """
    files = _mypy_config().get("files")
    assert files, "mypy `files` is empty; the type gate checks nothing"
    for entry in files:
        assert (ROOT / entry).is_file(), f"mypy `files` names a missing module: {entry}"


def test_lint_gate_keeps_the_error_handling_policy_rules_selected():
    """The rules `docs/error-handling.md` promises must stay selected.

    `BLE001`/`ARG` are the enforcement that page describes; `RUF100` (issue
    #219) is what stops the `# noqa` annotations it depends on from going
    stale — a directive that no longer suppresses anything is an unread claim
    about the code, not a global exemption. Dropping any of them turns the
    policy back into prose nobody checks.
    """
    config = tomllib.loads((ROOT / ".ruff.toml").read_text(encoding="utf-8"))
    selected = config["lint"]["select"]
    for rule in ("BLE", "ARG", "RUF100"):
        assert rule in selected, f".ruff.toml no longer selects {rule}"


def test_python_dependency_graph_is_locked_and_ci_installs_from_it():
    """Issue #146: the backend had no lockfile, so CI and a release built
    "the same commit" against whatever pip happened to resolve that week. The
    lock only helps if CI actually consumes it, so assert both halves: the
    file exists with real pins, and every job installs through `uv sync
    --frozen` rather than a bare `pip install -e` (a fresh resolve).
    """
    lock = ROOT / "uv.lock"
    assert lock.is_file(), "uv.lock is missing; the Python graph is unlocked again"
    text = lock.read_text(encoding="utf-8")
    assert 'name = "levh"' in text, "uv.lock does not lock the project itself"
    assert text.count("[[package]]") > 50, "uv.lock resolved implausibly few packages"
    # `uv.lock` is TOML and is the machine-readable contract; a parse failure
    # would make `uv sync --frozen` fail for every contributor.
    toml = tomllib.loads(text)
    assert toml.get("version"), "uv.lock has no lockfile format version"
    assert [p["name"] for p in toml["package"]].count("levh") == 1

    ci = (GITHUB / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "uv sync --frozen" in ci, "ci.yml no longer installs from the lockfile"
    assert "uv lock --check" in ci, (
        "ci.yml dropped the lockfile-freshness gate; a pyproject pin could "
        "change without anyone regenerating uv.lock"
    )
    assert 'pip install -e ".[dev]"' not in ci, (
        "ci.yml installs from pyproject's floor pins again, bypassing the lock"
    )


def test_pip_audit_scans_the_locked_graph_not_the_installed_environment():
    """Issue #146: the audit must measure the shipped graph. Freezing the
    installed environment audits whatever CI resolved from floor pins that day;
    `uv export --frozen` audits the exact pins in `uv.lock`. Assert the step
    exports rather than freezes, and that the exported file stays gitignored
    (#223 covers the ignore rule; this ties it to the new source).
    """
    ci = (GITHUB / "workflows" / "ci.yml").read_text(encoding="utf-8")
    if "resolved-requirements.txt" not in ci:
        pytest.skip("ci.yml no longer exports a resolved graph for auditing")

    assert "uv export --frozen" in ci, "the audit no longer reads the lockfile"
    assert "pip freeze" not in ci, (
        "the audit froze the installed environment again; that graph is not "
        "the one the lockfile pins"
    )
    assert "pip-audit" in ci and "--strict" in ci, "the strict CVE gate is gone"


# --- SAST and SBOM gates (issue #236) --------------------------------------
#
# #146's security items were deferred like #147's type check, and a gate that is
# only described in a workflow comment is one edit away from disappearing. These
# assert the steps exist, that the tools are pinned in the dev extra the same way
# ruff and mypy are, and — for SAST — that no unreviewed Medium finding can ride
# along unnoticed.


def _dev_extra() -> list[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return project["project"]["optional-dependencies"]["dev"]


def test_bandit_sast_is_a_ci_gate_and_is_pinned_like_ruff():
    """The SAST item from #146 is real only if CI runs it. Bandit installs from
    the locked dev extra rather than a fresh `uvx` resolve, so a new bandit
    release cannot add checks under the gate without a reviewed version bump.
    """
    ci = (GITHUB / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "uv run --frozen bandit" in ci, "ci.yml no longer runs bandit"

    pinned = next((re.search(r"bandit==(\d+\.\d+\.\d+)", req) for req in _dev_extra() if "bandit" in req), None)
    assert pinned, "the dev extra no longer pins an exact bandit version"


def test_sast_gate_is_medium_and_no_unreviewed_finding_survives():
    """Bandit's gate is Medium and above: the Low tier is dominated by
    B404/B603/B607 on the lifecycle hooks, which deliberately shell out, so
    gating there would force `# nosec` on code nobody is worried about and
    train reviewers to ignore the annotations. The Medium findings are all
    reviewed false positives (SQL assembled from `?` placeholders with the
    values bound, and the loopback health probe) and carry `# nosec <id>` at
    the site — so the run below must be empty. A new Medium fails here until it
    is either fixed or reviewed and annotated, which is the ratchet.
    """
    ci = (GITHUB / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "bandit -r server -ll" in ci, (
        "the SAST step no longer gates at Medium (`-ll`); gating at Low would "
        "mostly flag the hooks' deliberate subprocess calls"
    )

    result = subprocess.run(
        [sys.executable, "-m", "bandit", "-r", "server", "-ll", "-q", "-f", "json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.stdout, f"bandit produced no JSON (exit {result.returncode}): {result.stderr[-500:]}"
    findings = json.loads(result.stdout)["results"]
    assert findings == [], (
        "bandit reports Medium+ findings that were never reviewed; fix them or, "
        "if they are false positives, annotate the site with `# nosec <id>` and "
        "a reason:\n"
        + "\n".join(f"  {f['test_id']} {f['filename']}:{f['line_number']}" for f in findings)
    )


# Bandit reports a finding at the *start* line of the offending node but looks
# for `# nosec` on the node's *last* line. The SQL here is built as adjacent
# f-string fragments, so an annotation on the first fragment is honoured while
# bandit still prints `nosec encountered (...), but no failed test` — that
# warning says nothing about whether the annotation is still needed. The real
# staleness check is to re-run with `--ignore-nosec` (which reports the
# suppressed set anyway) and match each annotation against a finding whose node
# span covers it. `# nosec` is unconditional, so a stale one would silently
# absorb a future finding at the same line — the ruff-side RUF100 ratchet (#219)
# has no bandit counterpart without this.
_NOSEC_RE = re.compile(r"#\s*nosec\b(?P<rest>[^\n]*)")


def _nosec_sites() -> dict[tuple[str, int], set[str]]:
    """Map each `# nosec` site to the bandit rules it names (empty set if none)."""
    sites: dict[tuple[str, int], set[str]] = {}
    for path in sorted((ROOT / "server").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            match = _NOSEC_RE.search(line)
            if not match:
                continue
            # The reason text after the ids ("- placeholders are ...") must not
            # be mistaken for rule ids.
            listed = re.match(r"\s*(B\d+(?:\s*,\s*B\d+)*)", match.group("rest"))
            relative = path.relative_to(ROOT).as_posix()
            sites[(relative, lineno)] = set(re.findall(r"B\d+", listed.group(1))) if listed else set()
    return sites


def test_sast_nosec_annotations_all_still_suppress_a_live_finding():
    """Every `# nosec <id>` in `server/` must sit on a node that bandit still
    flags for that id without the annotation. A bare `# nosec` (no id) is
    rejected too: it would absorb any future finding regardless of rule.
    """
    sites = _nosec_sites()
    assert sites, "no `# nosec` annotations found; did the SAST sites move?"

    bare = [f"{path}:{line}" for (path, line), ids in sites.items() if not ids]
    assert bare == [], f"`# nosec` without a rule id absorbs any future finding: {bare}"

    result = subprocess.run(
        [sys.executable, "-m", "bandit", "-r", "server", "-ll", "-q", "-f", "json", "--ignore-nosec"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.stdout, f"bandit produced no JSON (exit {result.returncode}): {result.stderr[-500:]}"
    findings = json.loads(result.stdout)["results"]

    def suppresses(rule: str, path: str, line: int) -> bool:
        for finding in findings:
            if finding["test_id"] != rule or finding["filename"] != path:
                continue
            span = finding.get("line_range") or [finding["line_number"]]
            if span[0] <= line <= span[-1]:
                return True
        return False

    stale = [
        f"{path}:{line} ({rule})"
        for (path, line), ids in sorted(sites.items())
        for rule in sorted(ids)
        if not suppresses(rule, path, line)
    ]
    assert stale == [], (
        "these `# nosec` annotations no longer suppress a Medium+ finding that "
        "the SAST gate would otherwise report, so they would silently hide a "
        "future finding at the same line; remove them:\n"
        + "\n".join(f"  {entry}" for entry in stale)
    )


def test_sbom_is_built_in_ci_attached_to_the_release_and_pinned():
    """The SBOM item from #146: a BOM that is generated only locally, or only
    into a transient CI artifact, cannot be matched to a shipped tag. CI must
    build both halves (the locked Python graph and the frontend's installed
    packages) and publish.yml must attach them to the release.
    """
    ci = (GITHUB / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "cyclonedx-py" in ci, "ci.yml no longer generates a Python SBOM"
    assert "npm sbom --sbom-format cyclonedx" in ci, "ci.yml no longer generates a frontend SBOM"
    assert "uv export --frozen" in ci, "the SBOM no longer reads the lockfile"

    pinned = next((re.search(r"cyclonedx-bom==(\d+\.\d+\.\d+)", req) for req in _dev_extra() if "cyclonedx" in req), None)
    assert pinned, "the dev extra no longer pins an exact cyclonedx-bom version"

    publish = (GITHUB / "workflows" / "publish.yml").read_text(encoding="utf-8")
    for artifact in ("sbom-python.json", "sbom-frontend.json"):
        assert artifact in publish, (
            f"publish.yml no longer attaches {artifact} to the release"
        )


def _unreleased_section() -> str:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(r"^## Unreleased\s*$", changelog, flags=re.MULTILINE)
    assert match, "CHANGELOG.md has no `## Unreleased` heading"
    rest = changelog[match.end():]
    next_version = re.search(r"^## ", rest, flags=re.MULTILINE)
    return rest[: next_version.start()] if next_version else rest


def test_changelog_unreleased_section_is_not_empty():
    """The release notes are read straight from the `## Unreleased` section
    (`publish.yml` `awk -v v="## $VERSION"`), so a change that merges without an
    entry there is invisible in the release. That happened for #237 and #234
    (issue #239): the heading list still held only #193/#195/#144 while two
    user-visible merges had landed. Ruff catches a stale `noqa`; this is the
    cheapest equivalent for the changelog — the section must not be empty.
    """
    section = _unreleased_section()
    assert re.search(r"^###\s+\S", section, flags=re.MULTILINE), (
        "the `## Unreleased` section has no `### <sentence> (#issue)` heading; "
        "release notes are taken from this section verbatim"
    )
    assert re.search(r"^\s*[-*]\s+\S", section, flags=re.MULTILINE), (
        "the `## Unreleased` section heading(s) carry no explanatory bullet"
    )


def test_changelog_has_exactly_one_unreleased_heading():
    """A released section must be given its version heading.

    `_unreleased_section` above reads only the *first* `## Unreleased` heading,
    so a second one is invisible to the suite and to `publish.yml`'s
    `awk -v v="## $VERSION"`, which takes the text between two `##` headings.
    That is how 2.30.0's release notes sat under a stale second `## Unreleased`
    while the tree showed no `## 2.30.0` section at all (issue #250): the
    release notes rendered, but the changelog claimed the changes were still
    unreleased and `release.py` risked folding them into the *next* bump.
    Exactly one `## Unreleased` heading keeps "what is unreleased" unambiguous.
    """
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## Unreleased\s*$", changelog, flags=re.MULTILINE)
    assert len(headings) == 1, (
        f"CHANGELOG.md has {len(headings)} `## Unreleased` headings; exactly one "
        "section may be unreleased, so a second one is either a released "
        "section missing its `## <version>` heading or a duplicate"
    )
