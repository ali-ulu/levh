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
