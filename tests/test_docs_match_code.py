"""The reference docs must describe the code that exists.

Three tables drifted quietly until a release forced a look at them: the REST
reference was missing 40 endpoints, the CLI reference most of its commands,
and the tool list two. Documentation nobody can trust is worse than none, and
the only thing that keeps a hand-written table honest is a test.

A fourth drift lives in prose rather than a table: `docs/error-handling.md`
quoted a hand-maintained count of `except Exception` sites in `server/` and
kept it after the code moved on (issue #221). The same rule applies inward: an
internal inventory that dates itself has to either stay fresh or admit it is an
archive (issue #217).
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import tomllib
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest

from server.core.env import accepted_env_var_names

DOCS = Path(__file__).resolve().parent.parent / "docs"
SERVER = Path(__file__).resolve().parent.parent / "server"


def _shipped_server_python_files() -> list[Path]:
    """Every `server/*.py` a checkout of this commit actually contains.

    These inventories describe the code that ships, and a checkout of a commit
    holds tracked files and nothing else. Walking the directory instead counts
    whatever a neighbouring process left in the same working tree: on a machine
    where two agents share one checkout, an untracked `server/core/sentinel.py`
    lifted the derived count from 61 to 68 and turned the gate red on a pull
    request that had not touched error handling (observed 2026-09-29 while
    opening #325, where the branch's own number was right all along).

    Falls back to the directory walk where git cannot answer - a source tarball,
    an export with no index - because an unavailable answer must not be read as
    "this codebase has no files", which would pass every gate below.

    `cwd` alone does not pin the repository: an inherited `GIT_DIR`,
    `GIT_WORK_TREE` or `GIT_INDEX_FILE` would point `ls-files` at another
    checkout without moving the answer, so the environment's `GIT_*` variables
    are stripped and the call reads only the tree this test file sits in.
    """
    root = SERVER.parent
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        listing = subprocess.run(
            ["git", "ls-files", "-z", "--", "server"],
            cwd=root,
            env=env,
            capture_output=True,
            timeout=20,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return sorted(SERVER.rglob("*.py"))
    names = [n for n in listing.stdout.decode("utf-8", "replace").split("\0") if n.endswith(".py")]
    if not names:
        return sorted(SERVER.rglob("*.py"))
    return sorted(root / name for name in names)


#: Resolved once: the git call is cheap, and every inventory below has to agree
#: on the same tree, or two gates would disagree about what `server/` is.
SERVER_PY_FILES = _shipped_server_python_files()


def _normalize(path: str) -> str:
    """Path with its parameter names erased: /a/{id} and /a/{key} are one shape."""
    return re.sub(r"\{[^}]+\}", "{}", path)


# ── REST ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def api_paths() -> set[str]:
    from server.api import app

    return {_normalize(p) for p in app.openapi()["paths"]}


@pytest.fixture(scope="module")
def api_doc() -> str:
    return (DOCS / "api-reference.md").read_text(encoding="utf-8")


# Routes are documented as table rows, and the app serves paths outside the
# /api namespace too (the librarian's page and its widget script). Reading the
# rows — the same extraction the "invents no routes" test below uses — is what
# lets this test see them; matching on the /api prefix made any non-/api route
# impossible to document rather than merely undocumented.
_DOC_ROW_RE = re.compile(r"^\| [A-Z]+ \| `(/[^`]+)`", re.M)


def test_every_route_is_documented(api_paths, api_doc):
    documented = {_normalize(m) for m in _DOC_ROW_RE.findall(api_doc)}
    missing = sorted(api_paths - documented)
    assert not missing, f"undocumented routes: {missing}"


def test_the_api_doc_lists_no_route_twice(api_doc):
    """A route documented twice escapes both checks above: each copy names a
    served path, so nothing is missing and nothing is invented.

    The five librarian rows were duplicated for long enough that the two
    copies' descriptions drifted apart ("watcher" vs "librarian"), leaving a
    reader with two accounts of one endpoint and no way to tell which holds.
    """
    rows = re.findall(r"^\| ([A-Z]+) \| `(/[^`]+)`", api_doc, re.M)
    seen = Counter((method, _normalize(path)) for method, path in rows)
    duplicates = sorted(f"{method} {path}" for (method, path), n in seen.items() if n > 1)
    assert not duplicates, f"routes listed more than once: {duplicates}"


def test_the_api_doc_invents_no_routes(api_paths, api_doc):
    documented = {
        _normalize(m)
        for m in re.findall(r"^\| [A-Z]+ \| `(/[^`]+)`", api_doc, re.M)
    }
    # Neither of these is an HTTP route: the MCP app is mounted, and the
    # WebSocket has no OpenAPI schema.
    extra = sorted(documented - api_paths - {"/api/mcp/sse", "/ws/memory", "/ws/agents"})
    assert not extra, f"documented but not served: {extra}"


# ── CLI ──────────────────────────────────────────────────────────────


def test_every_cli_command_is_documented():
    from server.cli_parsers import build_parser

    parser, _ = build_parser("levh")
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    doc = (DOCS / "cli.md").read_text(encoding="utf-8")

    missing = [name for name in sub.choices if f"`levh {name}" not in doc]
    assert not missing, f"undocumented CLI commands: {sorted(missing)}"


def test_every_cli_subcommand_is_documented():
    from server.cli_parsers import build_parser

    parser, _ = build_parser("levh")
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    doc = (DOCS / "cli.md").read_text(encoding="utf-8")

    missing = []
    for name, child in sub.choices.items():
        kids = next(
            (a for a in child._actions if a.__class__.__name__ == "_SubParsersAction"), None
        )
        if kids:
            missing += [f"{name} {k}" for k in kids.choices if f"`levh {name} {k}`" not in doc]
    assert not missing, f"undocumented subcommands: {sorted(missing)}"


# ── MCP tools ────────────────────────────────────────────────────────


def test_the_tool_list_matches_the_registry():
    from server.tools.profiles import TOOL_TIERS

    doc = (DOCS / "mcp-tools.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"\| `([a-z_]+)` \|", doc))

    assert not set(TOOL_TIERS) - documented, (
        f"undocumented tools: {sorted(set(TOOL_TIERS) - documented)}"
    )
    assert not documented - set(TOOL_TIERS), (
        f"documented but unregistered: {sorted(documented - set(TOOL_TIERS))}"
    )


def test_the_profile_bands_in_the_docs_are_current():
    """The counts are quoted in prose, which is exactly what goes stale."""
    from server.tools.profiles import profile_counts

    counts = profile_counts()
    doc = (DOCS / "mcp-tools.md").read_text(encoding="utf-8")
    band = f'`minimal` ({counts["minimal"]}) ⊂ `work` ({counts["work"]}) ⊂ ' \
           f'`admin` ({counts["admin"]}) ⊂ `full` ({counts["full"]})'
    assert band in doc, f"stale profile bands; expected {band}"


# A test count is true only until the next test is added, and nothing makes a
# human update the sentence. `docs/testing.md` dropped its count (issue #150);
# the same sentence survived in `docs/ARCHITECTURE.md` and drifted from 122 to
# less than a ninth of the real suite (issue #194). Quote no number at all.
_TEST_COUNT_RE = re.compile(r"\b\d[\d,_]*\s+(?:passing\s+)?tests?\b", re.IGNORECASE)


def test_no_document_quotes_a_hand_maintained_test_count():
    offenders: list[str] = []
    for doc in sorted(DOCS.glob("*.md")):
        text = doc.read_text(encoding="utf-8", errors="replace")
        offenders += [f"{doc.name}: {m.group(0)!r}" for m in _TEST_COUNT_RE.finditer(text)]
    assert not offenders, (
        f"hand-maintained test counts in the published docs: {offenders}; "
        "quote no number, or derive it in CI"
    )


# The tool count is quoted in prose, which is exactly what goes stale: the
# README said 69 in its doc table while saying 73 four sections above, and the
# landing page still advertised 59. One number, three answers. The count now
# comes from the registry and is checked everywhere a reader meets it, so a
# tool added or removed cannot leave one of them behind.
_TOOL_COUNT_RE = re.compile(r"(\d+) (?:MCP )?tools")


def _published_prose_files() -> list[Path]:
    """Every reader-facing file that quotes a tool count.

    Root `*.md` (the README), the published `docs/*.md`, and `docs/index.html`
    — the landing page deployed to Pages, which is the first place a visitor
    meets the number and the last place anyone thought to update.

    `CHANGELOG.md` is excluded on purpose: it is a historical ledger, and each
    entry records the count that release actually shipped (39, 41, 59 …).
    Forcing those to today's number would falsify the history this check exists
    to protect.
    """
    root = [p for p in sorted(DOCS.parent.glob("*.md")) if p.name != "CHANGELOG.md"]
    return root + sorted(DOCS.glob("*.md")) + [DOCS / "index.html"]


def test_no_published_prose_quotes_a_stale_tool_count():
    from server.tools.profiles import profile_counts

    total = profile_counts()["full"]
    offenders: list[str] = []
    for path in _published_prose_files():
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        offenders += [
            f"{path.name}: {m.group(0)!r}"
            for m in _TOOL_COUNT_RE.finditer(text)
            if int(m.group(1)) != total
        ]
    assert not offenders, (
        f"stale tool counts (there are {total}): {offenders}; "
        "derive the number from server.tools.profiles instead of quoting it"
    )


# `docs/error-handling.md` announced the count of `except Exception` sites in
# `server/` as part of the BLE001 story; the number was written by hand, then
# the code grew and the sentence did not (issue #221). Derive it instead: the
# prose says "the N `except Exception` sites in `server/`", and N has to equal
# what an AST walk finds. Also assert the five re-raise sites the prose calls
# out, so the sentence is checked for the claim and not only the tally.
_EXCEPT_EXCEPTION_SITE_RE = re.compile(r"the (\d+) `except Exception` sites in `server/`")


def _except_exception_handlers() -> list[tuple[ast.ExceptHandler, str]]:
    """Every bare `except Exception:` clause in `server/`, with its source line."""
    handlers: list[tuple[ast.ExceptHandler, str]] = []
    for path in SERVER_PY_FILES:
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:  # a file we cannot parse is not a count
            raise AssertionError(f"could not parse {path}: {exc}") from exc
        lines = source.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            # `except (A, B):` names several types; only the bare `Exception`
            # alone counts, which is also the only shape BLE001 fires on.
            if getattr(node.type, "id", None) == "Exception":
                handlers.append((node, lines[node.lineno - 1]))
    return handlers


def _reraises(handler: ast.ExceptHandler) -> bool:
    """Any `raise` inside the clause: bare `raise` or a re-wrapped error."""
    return any(isinstance(node, ast.Raise) for node in ast.walk(handler))


def test_the_except_exception_count_in_the_error_handling_doc_is_current():
    doc = (DOCS / "error-handling.md").read_text(encoding="utf-8")
    quoted = [int(n) for n in _EXCEPT_EXCEPTION_SITE_RE.findall(doc)]
    assert quoted, "error-handling.md no longer states the except Exception count"
    actual = len(_except_exception_handlers())
    assert quoted == [actual], (
        f"error-handling.md quotes {quoted} except Exception sites; server/ has {actual}"
    )


def test_the_error_handling_doc_counts_the_reraising_sites():
    """The prose explains why some sites carry no `# noqa` by counting the
    ones that re-raise; derive that number so the claim stays true."""
    rethrows = sum(
        1
        for handler, line in _except_exception_handlers()
        if _reraises(handler) and "noqa: BLE001" not in line
    )
    doc = (DOCS / "error-handling.md").read_text(encoding="utf-8")
    assert f"{rethrows} of those" in doc, (
        f"error-handling.md does not mention the {rethrows} re-raising sites"
    )


# ── Environment variables ────────────────────────────────────────────

# `.env.example` is a hand-maintained inventory with no loader behind it, so
# the only thing that keeps it equal to the code is a test. Sixteen variables
# read by `server/` were absent from it — five of them (LEVH_AGENT,
# LEVH_AUTO_HEARTBEAT, LEVH_DASHBOARD_DIR, LEVH_EMBEDDER_DEBUG, LEVH_VERSION)
# from every document as well, so an operator could not learn they existed
# (issue #230). Only the canonical LEVH_* names are compared: the bare and
# legacy STACKMEMORY_* spellings are deliberately kept out of a template whose
# job is to teach the current names.
ENV_EXAMPLE = DOCS.parent / ".env.example"
_ENV_NAME_RE = re.compile(r"^LEVH_[A-Z0-9_]+$")


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Top-level ``NAME = "literal"`` bindings, for resolving env constants."""
    constants: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                constants[target.id] = node.value.value
    return constants


def _referenced_env_names() -> set[str]:
    """Canonical LEVH_* names ``server/`` reads, normalized to the LEVH_* form.

    Two sources, because the canonical name is spelled two ways in the code:

    * String literals matching ``LEVH_*`` — this catches names held in module
      constants (``ENABLED_ENV = "LEVH_DOGFOOD_ENABLED"``, ``CONFIG_PATH_ENV``),
      which is where several of the variables that were missing from the
      template actually live.
    * ``get_env(...)`` call sites, resolving a literal or a module-level string
      constant and normalizing it through ``accepted_env_var_names``. Without
      this the gate was blind to names read under a bare spelling — the code
      reads ``get_env("SQLITE_DB_PATH")`` and ``get_env(SYNONYMS_ENV)`` where
      ``SYNONYMS_ENV = "SYNONYMS_PATH"``, so ``LEVH_SQLITE_DB_PATH`` and
      ``LEVH_SYNONYMS_PATH`` were never compared against the template (issue
      #351). Normalizing with ``accepted_env_var_names`` rather than prefixing
      by hand keeps the gate and ``get_env`` from drifting apart.
    """
    names: set[str] = set()
    for path in SERVER_PY_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = _module_string_constants(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _ENV_NAME_RE.match(node.value):
                names.add(node.value)
                continue
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if called != "get_env" or not node.args:
                continue
            arg = node.args[0]
            literal = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else None
            if literal is None and isinstance(arg, ast.Name):
                literal = constants.get(arg.id)
            if literal:
                names.add(accepted_env_var_names(literal)[0])
    return names


def _template_env_names() -> set[str]:
    return set(re.findall(r"\bLEVH_[A-Z0-9_]+\b", ENV_EXAMPLE.read_text(encoding="utf-8")))


def test_every_referenced_env_var_is_in_the_template():
    missing = sorted(_referenced_env_names() - _template_env_names())
    assert not missing, (
        f"variables read by server/ but absent from .env.example: {missing}; "
        "an operator cannot discover a knob the template never lists"
    )


def test_the_env_template_lists_no_variable_the_code_never_reads():
    stale = sorted(_template_env_names() - _referenced_env_names())
    assert not stale, (
        f".env.example lists variables the code never reads: {stale}; "
        "the template would teach a setting that does nothing"
    )


def test_the_gate_sees_variables_read_under_a_bare_get_env_name():
    """A bare ``get_env("SQLITE_DB_PATH")`` is the ``LEVH_SQLITE_DB_PATH`` setting.

    The gate used to read only ``LEVH_*`` string literals, so a variable read
    exclusively under its bare spelling never reached the comparison — the
    template could omit it while the gate stayed green (issue #351).
    """
    referenced = _referenced_env_names()
    assert "LEVH_SQLITE_DB_PATH" in referenced, (
        "get_env('SQLITE_DB_PATH') should normalize to LEVH_SQLITE_DB_PATH; "
        "without this the template gate is blind to bare-spelled reads"
    )
    assert "LEVH_SYNONYMS_PATH" in referenced, (
        "get_env(SYNONYMS_ENV) with SYNONYMS_ENV = 'SYNONYMS_PATH' should "
        "resolve through the module constant to LEVH_SYNONYMS_PATH"
    )


# ── Coverage gates ───────────────────────────────────────────────────

# CI runs two numeric coverage gates (the total `--cov-fail-under` floor and
# the pull-request `diff-cover --fail-under` floor) that appeared with #206 and
# were described in no document at all (issue #222). A contributor could not
# learn the changed-lines rule or why the backend job needs `fetch-depth: 0`.
# The numbers now live in `docs/testing.md`; this test keeps them equal to the
# workflow so the prose cannot drift the way the gates themselves once did.
CI_WORKFLOW = DOCS.parent / ".github" / "workflows" / "ci.yml"
PYPROJECT = DOCS.parent / "pyproject.toml"
CONTRIBUTING = DOCS.parent / "CONTRIBUTING.md"

_TOTAL_FLOOR_RE = re.compile(r"--cov-fail-under=(\d+)")
_DIFF_FLOOR_RE = re.compile(r"--fail-under (\d+)")


def _configured_floors() -> tuple[int, int]:
    """The total and changed-lines coverage floors as the CI workflow sets them."""
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    total = _TOTAL_FLOOR_RE.findall(workflow)
    changed = _DIFF_FLOOR_RE.findall(workflow)
    assert total and changed, "ci.yml no longer runs both numeric coverage gates"
    return int(total[0]), int(changed[0])


def test_the_coverage_gates_are_documented():
    total, changed = _configured_floors()
    testing = (DOCS / "testing.md").read_text(encoding="utf-8")
    contributing = CONTRIBUTING.read_text(encoding="utf-8")

    assert "## Coverage gates" in testing, (
        "docs/testing.md has no Coverage gates section; CONTRIBUTING.md links to "
        "docs/testing.md#coverage-gates"
    )
    for name, text in (("docs/testing.md", testing), ("CONTRIBUTING.md", contributing)):
        assert f"{total}%" in text, f"{name} does not state the {total}% total floor"
        assert f"{changed}%" in text, f"{name} does not state the {changed}% changed-lines floor"
    assert f"--cov-fail-under={total}" in testing, (
        "docs/testing.md does not show the --cov-fail-under command"
    )
    assert f"--fail-under {changed}" in testing, (
        "docs/testing.md does not show the diff-cover --fail-under command"
    )
    assert "diff-cover" in testing and "fetch-depth: 0" in testing, (
        "docs/testing.md does not explain the diff-cover gate or its full checkout"
    )


def test_the_coverage_floors_match_the_coverage_config():
    """`--cov-fail-under` overrides `[tool.coverage.report] fail_under`, so a
    bare `pytest --cov` sees the config value; keeping it equal to CI avoids a
    second number the docs would have to track."""
    total, _ = _configured_floors()
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert config["tool"]["coverage"]["report"]["fail_under"] == total, (
        "[tool.coverage.report] fail_under disagrees with ci.yml's --cov-fail-under"
    )


# ── Clients ──────────────────────────────────────────────────────────


def test_every_supported_client_is_documented():
    from server.configs import PLATFORMS

    doc = (DOCS / "mcp-client-config.md").read_text(encoding="utf-8").lower()
    missing = [name for name in PLATFORMS if name.replace("_", " ") not in doc and name not in doc]
    assert not missing, f"undocumented clients: {sorted(missing)}"


# ── Links and branding ───────────────────────────────────────────────


def _markdown_files() -> list[Path]:
    root = DOCS.parent
    return sorted(root.glob("*.md")) + sorted(DOCS.glob("*.md"))


def _documentation_files() -> list[Path]:
    """Every Markdown file the published docs and the test docs ship."""
    root = DOCS.parent
    return _markdown_files() + sorted((root / "tests").rglob("*.md"))


def test_no_relative_documentation_link_is_broken():
    """A link to a file that does not exist is a promise the repo cannot keep.

    README pointed at `docs/demo/5-minute-demo.md` and ARCHITECTURE at
    `docs/product-hardening.md`; both were absent, and nothing noticed.
    """
    pattern = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")
    broken: list[str] = []
    for doc in _documentation_files():
        text = doc.read_text(encoding="utf-8", errors="replace")
        for match in pattern.finditer(text):
            link = match.group(1)
            if link.startswith(("http://", "https://", "mailto:")):
                continue
            if not (doc.parent / link).resolve().exists():
                broken.append(f"{doc.name}: {link}")
    assert not broken, f"broken relative links: {broken}"


# `tests/groundtruth/README.md` and the four Gate 0A test docstrings pointed at
# `evidence/groundtruth/task-00A{1..4}/harness/...`. Nothing tracked that
# directory, and the link check above only looked at `docs/*.md`, so the paths
# stayed broken while promising an in-repo audit harness that never existed.
_EVIDENCE_PATH_RE = re.compile(r"evidence/groundtruth/[A-Za-z0-9_./-]+")


def test_no_doc_points_at_the_untracked_evidence_workspace():
    offenders: list[str] = []
    for doc in _documentation_files():
        text = doc.read_text(encoding="utf-8", errors="replace")
        for path in _EVIDENCE_PATH_RE.findall(text):
            if not (DOCS.parent / path).exists():
                offenders.append(f"{doc.relative_to(DOCS.parent)}: {path}")
    assert not offenders, (
        "documentation references an untracked evidence/ harness path: "
        f"{offenders}"
    )


# A prose reference to a test file is a promise the reader can keep by opening
# it: `docs/mcp-client-config.md` kept pointing at `tests/test_mcp_configs.py`
# after the file was renamed to `tests/test_client_config_formats.py` (issue
# #252), so the doc sent the reader looking for a file that is not there. The
# link checker above does not see these because they are not Markdown links.
_TEST_FILE_REF_RE = re.compile(r"(?<![\w/.-])(tests/[A-Za-z0-9_./-]+\.py)")


def test_no_doc_points_at_a_test_file_that_does_not_exist():
    offenders: list[str] = []
    for doc in _documentation_files():
        text = doc.read_text(encoding="utf-8", errors="replace")
        for path in _TEST_FILE_REF_RE.findall(text):
            if not (DOCS.parent / path).exists():
                offenders.append(f"{doc.relative_to(DOCS.parent)}: {path}")
    assert not offenders, (
        f"documentation references a test file that does not exist: {offenders}"
    )


# The 2.x rename left the security docs telling operators to set a variable
# the code no longer reads. The failure mode is silent: setting
# STACKMEMORY_TOKEN leaves the server open because it only ever reads
# LEVH_TOKEN. Legacy names may still be *documented* as deprecated — the check
# is that every mention sits in a deprecation context, not in setup guidance.
_STALE_NAME_RE = re.compile(r"\bStackMemory\b|STACKMEMORY_[A-Z_]+")
_DEPRECATION_MARKERS = ("deprecat", "legacy", "backward compat")
_PUBLIC_DOCS = ["SECURITY.md", "CONTRIBUTING.md", "README.md"]


@pytest.mark.parametrize("doc_name", _PUBLIC_DOCS)
def test_public_docs_use_the_current_product_name_and_env_prefix(doc_name):
    text = (DOCS.parent / doc_name).read_text(encoding="utf-8")
    offenders: list[str] = []
    for paragraph in text.split("\n\n"):
        if not _STALE_NAME_RE.search(paragraph):
            continue
        lowered = paragraph.lower()
        if any(marker in lowered for marker in _DEPRECATION_MARKERS):
            continue
        offenders.extend(_STALE_NAME_RE.findall(paragraph))
    assert not offenders, (
        f"{doc_name} uses legacy names {sorted(set(offenders))} outside a "
        "deprecation note; the code reads LEVH_* only, so a reader following "
        "this doc would configure the wrong variable"
    )


# ── Internal vs published surface ────────────────────────────────────

# An internal inventory reads as a product claim when it reaches the published
# docs: it dates itself, counts the test suite, and lists open debt. The
# markers are the Turkish debt-inventory vocabulary the one existing file uses.
_INTERNAL_MARKERS = ("Karnesi", "Teknik Borç", "borç envanteri")


def test_internal_inventories_stay_out_of_the_published_docs():
    """`docs/*.md` is the published surface; maintainer scorecards belong under
    `docs/internal/`, where a reader does not mistake a dated gap list for
    documentation of how the product behaves (issue #150)."""
    leaked: list[str] = []
    for doc in sorted(DOCS.glob("*.md")):
        text = doc.read_text(encoding="utf-8", errors="replace")
        if any(marker in text for marker in _INTERNAL_MARKERS):
            leaked.append(doc.name)
    assert not leaked, (
        f"internal inventories in the published docs surface: {leaked}; "
        "move them under docs/internal/"
    )


def test_internal_docs_directory_is_described():
    """A reader who lands in docs/internal/ is told what it is and why."""
    readme = DOCS / "internal" / "README.md"
    assert readme.is_file(), "docs/internal/README.md is missing"
    assert readme.read_text(encoding="utf-8").strip()


# ── Freshness of dated internal inventories ──────────────────────────

# An inventory that dates itself goes stale silently: SOLID_KARNESI.md kept
# claiming "943 passed / 1 skipped" and "26.155 satır" while main moved on, and
# six of its findings had been closed (issue #217). Nothing kept it honest, so
# a dated file must either have been re-verified recently or say it is archived.
_ARCHIVE_BANNER = "ARŞİV"
_DATED_INVENTORY_MAX_AGE = timedelta(days=90)
_DATE_RE = re.compile(r"Tarih:\s*(\d{4}-\d{2}-\d{2})")


def _internal_inventories() -> list[Path]:
    return sorted((DOCS / "internal").glob("*.md"))


@pytest.mark.parametrize("doc", _internal_inventories(), ids=lambda p: p.name)
def test_dated_internal_inventory_is_fresh_or_archived(doc: Path):
    """A `Tarih:`-stamped inventory is a claim about the code today. Either its
    date is recent, or the file declares itself an archive the reader should not
    treat as the current debt state."""
    text = doc.read_text(encoding="utf-8")
    stamped = _DATE_RE.search(text)
    if not stamped or _ARCHIVE_BANNER in text:
        return
    age = date.today() - date.fromisoformat(stamped.group(1))
    assert age <= _DATED_INVENTORY_MAX_AGE, (
        f"{doc.name} is dated {stamped.group(1)} ({age.days} days old) and does not "
        f"declare itself an archive; re-verify its numbers or add an "
        f"'{_ARCHIVE_BANNER}' banner at the top"
    )


def test_archived_inventory_is_flagged_in_the_readme():
    """docs/internal/README.md is the entry point; a reader must learn there
    that an inventory is archived, not only inside the file itself."""
    readme = (DOCS / "internal" / "README.md").read_text(encoding="utf-8")
    for doc in _internal_inventories():
        if doc.name == "README.md":
            continue
        if _ARCHIVE_BANNER in doc.read_text(encoding="utf-8"):
            assert doc.name in readme and "Archived" in readme, (
                f"{doc.name} is archived but docs/internal/README.md does not say so"
            )
