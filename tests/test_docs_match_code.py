"""The reference docs must describe the code that exists.

Three tables drifted quietly until a release forced a look at them: the REST
reference was missing 40 endpoints, the CLI reference most of its commands,
and the tool list two. Documentation nobody can trust is worse than none, and
the only thing that keeps a hand-written table honest is a test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "docs"


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


def test_no_relative_documentation_link_is_broken():
    """A link to a file that does not exist is a promise the repo cannot keep.

    README pointed at `docs/demo/5-minute-demo.md` and ARCHITECTURE at
    `docs/product-hardening.md`; both were absent, and nothing noticed.
    """
    pattern = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")
    broken: list[str] = []
    for doc in _markdown_files():
        text = doc.read_text(encoding="utf-8", errors="replace")
        for match in pattern.finditer(text):
            link = match.group(1)
            if link.startswith(("http://", "https://", "mailto:")):
                continue
            if not (doc.parent / link).resolve().exists():
                broken.append(f"{doc.name}: {link}")
    assert not broken, f"broken relative links: {broken}"


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
