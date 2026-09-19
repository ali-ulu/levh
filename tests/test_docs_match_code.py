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
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "docs"
SERVER = Path(__file__).resolve().parent.parent / "server"


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


@pytest.mark.parametrize("doc_name", ["getting-started.md"])
def test_no_document_quotes_a_stale_tool_count(doc_name):
    from server.tools.profiles import profile_counts

    total = profile_counts()["full"]
    text = (DOCS / doc_name).read_text(encoding="utf-8")
    stale = [m for m in re.findall(r"(\d+) (?:MCP )?tools", text) if int(m) != total]
    assert not stale, f"{doc_name} quotes {stale} tools; there are {total}"


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
    for path in sorted(SERVER.rglob("*.py")):
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
