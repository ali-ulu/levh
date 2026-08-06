"""Free-text people/organization signals — recover entities from a memory's
content when no connector recorded them structurally.

``people.py`` reads people out of connector metadata (``attendees``, ``from``,
``speakers``, ...). That covers calendar/email/transcript imports but leaves
the most common case empty: a note typed by hand, a commit message, a captured
decision. Those memories produced *zero* entities, so the knowledge graph,
people/orgs views and the trust corroboration signal all stayed blank for
anyone who wasn't importing a mailbox.

This module closes that gap with deterministic, high-precision patterns only —
no NER model, no LLM, no I/O, consistent with the rest of the entity layer.
Precision is deliberately favoured over recall: a noisy graph is worse than a
sparse one, and a repo import must not mint an entity per capitalised token.

Signals, in order of confidence:
  1. e-mail addresses in the body      → person (+ organization via domain)
  2. ``@handle`` mentions              → person
  3. company-suffix names ("Acme Inc") → organization   (prose sources only)
  4. anchored relational phrasing      → person         (prose sources only)
     ("met with Ayşe", "call with Mert", "Elif ile görüştüm")

Signals 3-4 are skipped for code, because source files are full of
capitalised identifiers that look like names but are not.
"""

from __future__ import annotations

import re
from typing import Any

from .people import extract_people, parse_person

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# @handle — GitHub/Slack style. Anchored so e-mail local parts don't match.
_MENTION_RE = re.compile(r"(?<![\w@.-])@([A-Za-z][A-Za-z0-9_-]{1,38})\b")

# "Acme Inc.", "Zephyr Labs", "Contoso Technologies" — up to three leading
# capitalised words before a company-type suffix.
_ORG_SUFFIXES = (
    r"Inc|LLC|Ltd|GmbH|Corp|Co|BV|NV|SA|AG|AS|A\.Ş|AŞ|Ş\.T\.İ|Labs|Holdings?|"
    r"Technologies|Technology|Ventures|Partners|Group|Software|Systems|Solutions"
)
_ORG_RE = re.compile(
    r"\b((?:[A-ZÇĞİÖŞÜ][\w&'’-]*[ ]){0,2}[A-ZÇĞİÖŞÜ][\w&'’-]*[ ]"
    r"(?:" + _ORG_SUFFIXES + r")\b\.?)"
)

# Anchored person mentions. English "met with X" / Turkish "X ile görüştüm".
# The verb is matched case-insensitively; the name must stay capitalised, so
# the alternation spells both cases instead of using a whole-pattern flag.
_NAME = r"[A-ZÇĞİÖŞÜ][a-zçğıöşü]+(?:[ ][A-ZÇĞİÖŞÜ][a-zçğıöşü]+){0,2}"
_PERSON_EN_RE = re.compile(
    r"\b(?:[Mm]et|[Mm]eeting|[Cc]all|[Ss]poke|[Tt]alked|[Ss]ync|[Cc]hatted|"
    r"[Cc]atch[- ]up|[Ii]nterview|[Pp]aired)"
    r"(?:[ ]\w+){0,2}?[ ](?:with|to)[ ](" + _NAME + r")"
)
# Diacritics are optional — people often type Turkish without them.
_PERSON_TR_RE = re.compile(
    r"\b(" + _NAME + r")[ ]ile[ ]\w*?"
    r"(?:görüş|gorus|konuş|konus|toplan|sync)"
)

# Words that pass the capitalisation test but never name a person.
_NOT_A_PERSON = frozenset(
    {
        "the", "team", "client", "customer", "support", "everyone", "them",
        "him", "her", "us", "you", "me", "him/her", "all", "both", "someone",
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
        "sunday", "today", "tomorrow", "yesterday", "product", "design",
        "engineering", "sales", "marketing", "legal", "finance", "hr",
        "ekip", "takım", "müşteri", "herkes", "ekiple", "bugün", "yarın",
    }
)

# A memory that looks like source code: prose heuristics are unsafe there.
_CODE_EXTENSIONS = frozenset(
    {
        ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go",
        ".rs", ".java", ".kt", ".kts", ".c", ".h", ".cc", ".cpp", ".hpp",
        ".cs", ".rb", ".php", ".swift", ".m", ".scala", ".sh", ".bash",
        ".zsh", ".ps1", ".sql", ".css", ".scss", ".sass", ".less", ".html",
        ".htm", ".xml", ".vue", ".svelte", ".json", ".yaml", ".yml",
        ".toml", ".ini", ".cfg", ".lock",
    }
)
_CODE_SOURCES = ("local_files", "github")


# Line shapes that only occur in code, not prose.
_CODE_LINE_RE = re.compile(
    r"^\s*(?:def |class |import |from \w+ import |function |const |let |var |"
    r"public |private |func |fn |#include|package |return |@\w+\(|</?\w+>|"
    r"\w+\s*[:=]\s*(?:function|\(|\{|\[)|[})];?\s*$)"
)


def _content_looks_like_code(content: str) -> bool:
    """Conservative sniff for memories whose metadata didn't mark them as code
    (a pasted snippet, or a connector that recorded no extension)."""
    lines = [line for line in content.split("\n") if line.strip()]
    if len(lines) < 3:
        return False
    hits = sum(1 for line in lines if _CODE_LINE_RE.match(line))
    return hits >= 3 or hits >= len(lines) * 0.3


def looks_like_code(source: str, metadata: dict, content: str = "") -> bool:
    """True when this memory is (part of) source code rather than prose."""
    extension = str(metadata.get("extension") or "").lower()
    if extension in _CODE_EXTENSIONS:
        return True
    if any(s in source for s in _CODE_SOURCES):
        # A code-ish connector with no extension recorded: fall back to the path.
        for key in ("relative_path", "file_path", "file_name", "path"):
            value = metadata.get(key)
            if isinstance(value, str) and "." in value:
                suffix = value[value.rfind(".") :].lower()
                if suffix in _CODE_EXTENSIONS:
                    return True
    return _content_looks_like_code(content)


def _clean_name(raw: str) -> str:
    return " ".join(raw.replace("’", "'").split()).strip(" .,;:!?-'\"")


# Company-type words, lowercased, for rejecting orgs matched by the "met with
# X" phrasing — you meet with Zephyr Labs, but it is not a person.
_ORG_WORDS = frozenset(
    part.replace("\\", "").rstrip("?").lower() for part in _ORG_SUFFIXES.split("|")
) | {"labs", "holding", "holdings", "team", "group"}


def _is_plausible_person(name: str) -> bool:
    parts = name.split()
    if len(name) < 2 or not parts:
        return False
    if any(part.lower() in _NOT_A_PERSON for part in parts):
        return False
    # "Zephyr Labs", "Acme Systems" — an organization, not a person.
    return parts[-1].lower() not in _ORG_WORDS


def extract_people_from_text(content: str, source: str = "", metadata: dict | None = None) -> list[tuple[str, str]]:
    """Return ``(name, email)`` people mentioned in free text.

    Mirrors ``people.extract_people``'s return shape so text-derived people
    flow through the existing people/organization/entity pipeline unchanged.
    """
    if not content:
        return []
    metadata = metadata if isinstance(metadata, dict) else {}
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(name: str, email: str) -> None:
        key = (email or name).lower()
        if not key or key in seen:
            return
        seen.add(key)
        out.append((name, email))

    for match in _EMAIL_RE.finditer(content):
        parsed = parse_person(match.group(0))
        if parsed:
            _add(*parsed)

    for match in _MENTION_RE.finditer(content):
        handle = match.group(1)
        if handle.lower() not in _NOT_A_PERSON:
            _add(handle, "")

    if looks_like_code(source, metadata, content):
        return out

    for pattern in (_PERSON_EN_RE, _PERSON_TR_RE):
        for match in pattern.finditer(content):
            name = _clean_name(match.group(1))
            if _is_plausible_person(name):
                _add(name, "")

    return out


def extract_orgs_from_text(content: str, source: str = "", metadata: dict | None = None) -> list[str]:
    """Return organization display names named outright in free text."""
    if not content:
        return []
    metadata = metadata if isinstance(metadata, dict) else {}
    if looks_like_code(source, metadata, content):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for match in _ORG_RE.finditer(content):
        name = _clean_name(match.group(1))
        if len(name) < 2 or name.lower() in _NOT_A_PERSON:
            continue
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(name)
    return out


def people_in_memory(memory: Any) -> list[tuple[str, str]]:
    """All ``(name, email)`` people for one memory: connector metadata first,
    then anything recoverable from the content itself.

    Metadata wins on identity collisions, because structural fields carry a
    role (attendee, sender) that free text does not.
    """
    metadata = getattr(memory, "metadata", None) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    found = list(extract_people(metadata))
    seen = {(email or name).lower() for name, email in found}

    for name, email in extract_people_from_text(
        getattr(memory, "content", "") or "",
        getattr(memory, "source", None) or "",
        metadata,
    ):
        key = (email or name).lower()
        if key not in seen:
            seen.add(key)
            found.append((name, email))
    return found
