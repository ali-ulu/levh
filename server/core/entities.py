"""Entity extraction — turn a memory into the typed entities it references,
for the persistent knowledge graph (Faz 2 entity layer).

Where ``people.py`` / ``organizations.py`` aggregate metadata on the fly, this
module produces a flat list of typed entities per memory that the engine
persists into the ``entities`` + ``memory_entities`` tables, so the graph can
answer "which memories mention entity X" and "which entities co-occur with X"
with a real join instead of a full re-scan.

Entity types: person, organization, event, document, task. Pure, deterministic,
no I/O, no LLM.
"""

from __future__ import annotations

import re
from typing import Any

from .organizations import FREE_EMAIL_DOMAINS, domain_to_org
from .text_entities import extract_orgs_from_text, people_in_memory

# Sources / metadata that mark a memory as a document.
_DOCUMENT_SOURCES = ("notion", "obsidian", "local_files", "github")
_DOCUMENT_META_KEYS = (
    "path",
    "file",
    "filename",
    "document",
    "title_path",
    "relative_path",
    "file_path",
    "file_name",
)

# Task / action-item markers (English + Turkish) — same spirit as the
# commitment detector, but these become first-class task entities.
_TASK_PATTERN = re.compile(
    r"\bTODO\b|\baction item\b|\bneed to\b|\bfollow[- ]?up\b|\bI['’]?ll\b|"
    r"\bI will\b|\bwe['’]ll\b|\bwe will\b|\bgoing to\b|"
    r"yapacağ|göndereceğ|halledeceğ|takip ed",
    re.IGNORECASE,
)


def _key(text: str) -> str:
    return (text or "").strip().lower()[:120]


def _first_line(content: str) -> str:
    return (content or "").split("\n", 1)[0].strip()


def _squash(text: str) -> str:
    """Alphanumerics only, lowercased — so "Zephyr Labs" and the
    domain-derived "Zephyrlabs" compare equal."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _is_event(source: str, metadata: dict) -> bool:
    if isinstance(metadata.get("attendees"), list) and metadata.get("attendees"):
        return True
    if "calendar" in source or "transcript" in source:
        return True
    return bool(metadata.get("title") and metadata.get("captured_at"))


def _document_name(source: str, metadata: dict, content: str) -> str | None:
    for k in _DOCUMENT_META_KEYS:
        v = metadata.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    if any(s in source for s in _DOCUMENT_SOURCES):
        title = metadata.get("title")
        return title.strip() if isinstance(title, str) and title.strip() else _first_line(content)
    return None


def _task_text(content: str) -> str | None:
    """The sentence that makes this memory a task, if any."""
    if not content or not _TASK_PATTERN.search(content):
        return None
    segments: list[str] = []
    for line in content.split("\n"):
        segments.extend(line.split(". "))
    sentence = next((s for s in segments if _TASK_PATTERN.search(s)), content)
    return sentence.strip()[:160] or None


def extract_entities(memory: Any) -> list[dict]:
    """All typed entities referenced by one memory.

    ``memory`` is duck-typed: ``.content``, ``.metadata``, ``.source``. Returns
    a list of ``{type, key, name, role}`` dicts, de-duplicated within the memory
    by ``(type, key)``.
    """
    metadata = getattr(memory, "metadata", None) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    content = getattr(memory, "content", "") or ""
    source = getattr(memory, "source", None) or ""

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add(etype: str, key: str, name: str, role: str) -> None:
        k = _key(key)
        if not k or (etype, k) in seen:
            return
        seen.add((etype, k))
        out.append({"type": etype, "key": k, "name": name.strip() or k, "role": role})

    # people + organizations — connector metadata first, then whatever the
    # content itself names (so hand-typed notes aren't entity-less).
    domain_org_labels: set[str] = set()
    for name, email in people_in_memory(memory):
        person_key = email or name.lower()
        _add("person", person_key, name, "person")
        if email:
            domain = email.split("@")[-1].lower()
            if domain and domain not in FREE_EMAIL_DOMAINS:
                label = domain_to_org(domain)
                domain_org_labels.add(_squash(label))
                _add("organization", domain, label, "org")

    # Organizations named outright in prose ("Zephyr Labs"), which have no
    # e-mail domain to key off. Skipped when this memory already produced the
    # same company from an e-mail domain — the domain is the stabler identity,
    # so "Zephyr Labs" folds into zephyrlabs.io rather than forking a node.
    for org_name in extract_orgs_from_text(content, source, metadata):
        if _squash(org_name) in domain_org_labels:
            continue
        _add("organization", org_name, org_name, "org")

    # event
    if _is_event(source, metadata):
        title = metadata.get("title")
        name = title.strip() if isinstance(title, str) and title.strip() else _first_line(content)
        if name:
            _add("event", name, name, "event")

    # document
    doc_name = _document_name(source, metadata, content)
    if doc_name:
        _add("document", doc_name, doc_name, "document")

    # task
    task = _task_text(content)
    if task:
        _add("task", task, task, "task")

    return out
