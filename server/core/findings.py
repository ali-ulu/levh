"""Findings: scrubbing and fingerprinting.

Two pure functions sit between "a watcher noticed something" and the row that
lands in the inbox, and both exist for a reason a reader should not have to
guess at.

``scrub`` runs first. A finding's evidence is drawn from this machine: file
paths carry the operator's username, tracebacks carry home directories, shell
output carries whatever was in the environment. The inbox itself is local, but
a finding is written to be *forwarded* — pasted into an issue, mailed, shown in
a screenshot — and a value that was never in the text cannot leak from it
later. Scrubbing at write time, rather than at export time, means every path
out of the inbox is covered by construction instead of by remembering.

``fingerprint`` runs second. The reporter is a periodic loop, so the same
problem arrives again and again; the fingerprint is what makes the tenth
sighting update one row instead of adding a tenth. It deliberately ignores the
parts of the evidence that vary between sightings — line numbers, timestamps,
ids, memory counts — so "the same problem" means the same problem, not the
same bytes.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from server.core.admission import redact_secrets

# Longest-first so /Users/<name>/x is replaced before a bare home fragment.
_USER_PATH_RE = re.compile(
    r"(?i)(?:[a-z]:[\\/]+users[\\/]+|/home/|/Users/)([^\\/\s\"'<>|:]+)"
)

# Volatile parts of an otherwise identical report. Stripped for the
# fingerprint only — the stored detail keeps them, because they are exactly
# what a human needs when they open the finding.
_VOLATILE_PATTERNS = [
    re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"),
    re.compile(r"(?i)\bline\s+\d+"),
    re.compile(r"\b[0-9a-f]{8,}\b"),          # hashes, uuids, object ids
    re.compile(r"0x[0-9a-fA-F]+"),            # memory addresses
    re.compile(r"\b\d+\b"),                   # counts, ports, sizes
]

_PLACEHOLDER_HOME = "<HOME>"
_PLACEHOLDER_USER = "<USER>"


def scrub(text: str) -> str:
    """Strip machine-identifying and secret material from finding text.

    Order matters: secrets are redacted first (their patterns can span a path),
    then the home directory as a literal, then any remaining user-shaped path.
    """
    if not text:
        return text

    cleaned, _ = redact_secrets(text)

    home = str(Path.home())
    if home:
        cleaned = re.sub(re.escape(home), _PLACEHOLDER_HOME, cleaned, flags=re.IGNORECASE)
        # The same path with forward slashes is the same path.
        cleaned = re.sub(
            re.escape(home.replace("\\", "/")), _PLACEHOLDER_HOME, cleaned,
            flags=re.IGNORECASE,
        )

    cleaned = _USER_PATH_RE.sub(lambda _m: _PLACEHOLDER_HOME + "/", cleaned)

    # The bare username can appear outside any path (log lines, agent ids).
    username = os.getenv("USERNAME") or os.getenv("USER") or ""
    if len(username) >= 3:
        cleaned = re.sub(
            rf"(?i)\b{re.escape(username)}\b", _PLACEHOLDER_USER, cleaned
        )

    return cleaned


def fingerprint(category: str, title: str, detail: str) -> str:
    """A stable 12-hex id for "this same problem".

    Two sightings of one problem must land on one id even when their evidence
    differs in the volatile parts; two different problems must not collide.
    Built from the category and title (which the reporter controls and keeps
    stable) plus the *shape* of the detail with volatile spans removed.
    """
    shape = detail or ""
    for pattern in _VOLATILE_PATTERNS:
        shape = pattern.sub("#", shape)
    shape = re.sub(r"\s+", " ", shape).strip().lower()

    basis = f"{category.strip().lower()}|{title.strip().lower()}|{shape}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


VALID_CATEGORIES = {"bug", "config", "memory", "agent", "other"}
VALID_SEVERITIES = {"critical", "high", "medium", "low"}


def build_row(
    title: str,
    detail: str,
    category: str = "other",
    severity: str = "medium",
    source: str = "librarian",
) -> dict:
    """Normalize a raw report into the row shape ``record_finding`` stores.

    Unknown categories and severities are coerced rather than rejected: the
    reporter is often an LLM, and losing a real finding to a typo'd enum is a
    worse failure than filing it under ``other``.
    """
    clean_title = scrub(title).strip()[:200]
    clean_detail = scrub(detail).strip()[:8000]
    cat = category.strip().lower()
    sev = severity.strip().lower()
    if cat not in VALID_CATEGORIES:
        cat = "other"
    if sev not in VALID_SEVERITIES:
        sev = "medium"
    return {
        "id": fingerprint(cat, clean_title, clean_detail),
        "title": clean_title,
        "detail": clean_detail,
        "category": cat,
        "severity": sev,
        "source": (source or "unknown").strip()[:64],
    }
