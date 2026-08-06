"""Organizations aggregation — group the people graph by email domain.

Faz 2 entity layer: where ``people.py`` rolls captured metadata up into
distinct *people*, this module rolls the same people up one level further
into distinct *organizations* — "which companies do I actually interact
with, how often, and who from them?". It reuses
``text_entities.people_in_memory`` for the underlying (name, email)
extraction so there is exactly one place that understands both the metadata
shapes connectors produce and the people named in free text. Pure functions,
no I/O.

Organizations here are keyed by e-mail domain. Companies named in prose with
no address to key off ("Zephyr Labs") become organization entities in the
knowledge graph via ``entities.py``, but do not appear in this rollup.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

# Public suffix / generic-TLD-ish labels to peel off the end of a domain
# before picking the "organization" label. Not a full public-suffix-list
# implementation — deliberately simple and deterministic per the spec.
_SUFFIXES = {
    "com", "org", "net", "io", "co", "ai", "dev", "app", "gov", "edu",
    "uk", "de", "fr", "tr", "us",
}

# Generic subdomain labels that never identify an organization on their own.
_GENERIC_SUBDOMAINS = {"www", "mail", "smtp", "imap", "email"}

# Free/personal email providers are excluded from org grouping — they are
# not organizations.
FREE_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "ymail.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "icloud.com",
        "me.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "gmx.com",
        "mail.com",
        "yandex.com",
        "zoho.com",
        "fastmail.com",
        "hey.com",
    }
)


def domain_to_org(domain: str) -> str:
    """Turn an email domain into a display organization name.

    Strips a leading ``www.``, peels known public-suffix-ish TLD labels off
    the end, then picks the last remaining label that isn't a generic
    subdomain (``mail``, ``smtp``, ...) and Title-Cases it. E.g.
    ``acme.com`` -> "Acme", ``mail.acme.co.uk`` -> "Acme",
    ``bbc.co.uk`` -> "Bbc". Falls back to the raw domain if nothing
    survives (fully generic/suffix-only input).
    """
    if not domain:
        return domain
    raw = domain.strip()
    d = raw.lower()
    if d.startswith("www."):
        d = d[4:]
    labels = [label for label in d.split(".") if label]

    while len(labels) > 1 and labels[-1] in _SUFFIXES:
        labels.pop()

    name: Optional[str] = None
    for label in reversed(labels):
        if label and label not in _GENERIC_SUBDOMAINS:
            name = label
            break

    return (name or raw).title()


def aggregate_organizations(memories: Iterable[Any]) -> list[dict]:
    """Roll a set of memories up into distinct organizations by email domain.

    ``memories`` are objects with ``.id``, ``.metadata``, ``.source``,
    ``.created_at`` (Memory models or anything duck-typed the same). People
    with no email, or whose email domain is a free/personal provider, are
    excluded — they don't identify an organization. Returns a list of
    organization dicts sorted by memory_count desc, then name. Each org
    carries an internal ``memory_ids`` list (mirroring ``people.py``'s
    convention) for callers that need to look the referencing memories up;
    summary views should drop it.
    """
    # Imported here, not at module scope: ``text_entities`` reuses
    # ``parse_person`` from ``people``, which this module imports from.
    from .text_entities import people_in_memory

    orgs: dict[str, dict] = {}
    for mem in memories:
        found = people_in_memory(mem)
        if not found:
            continue
        source = getattr(mem, "source", None)
        created = getattr(mem, "created_at", "") or ""
        mem_id = getattr(mem, "id", None)

        # Dedupe within this memory: one memory counts once per org, and
        # within an org we keep the longest (most descriptive) display name
        # per email — same "longest-name-wins" convention as people.py.
        mem_org_names: dict[str, dict[str, str]] = {}
        for name, email in found:
            if not email:
                continue
            domain = email.split("@")[-1].lower()
            if not domain or domain in FREE_EMAIL_DOMAINS:
                continue
            bucket = mem_org_names.setdefault(domain, {})
            existing = bucket.get(email)
            if existing is None or len(name) > len(existing):
                bucket[email] = name

        for domain, names_by_email in mem_org_names.items():
            entry = orgs.get(domain)
            if entry is None:
                entry = {
                    "key": domain,
                    "name": domain_to_org(domain),
                    "domain": domain,
                    "memory_count": 0,
                    "memory_ids": [],
                    "names_by_email": {},
                    "sources": set(),
                    "last_seen": "",
                }
                orgs[domain] = entry
            entry["memory_count"] += 1
            entry["memory_ids"].append(mem_id)
            if source:
                entry["sources"].add(source)
            if created > entry["last_seen"]:
                entry["last_seen"] = created
            for email, name in names_by_email.items():
                cur = entry["names_by_email"].get(email)
                if cur is None or len(name) > len(cur):
                    entry["names_by_email"][email] = name

    result = []
    for entry in orgs.values():
        people_names = sorted(set(entry["names_by_email"].values()))
        result.append(
            {
                "key": entry["key"],
                "name": entry["name"],
                "domain": entry["domain"],
                "memory_count": entry["memory_count"],
                "memory_ids": entry["memory_ids"],
                "people": people_names,
                "person_count": len(entry["names_by_email"]),
                "sources": sorted(entry["sources"]),
                "last_seen": entry["last_seen"],
            }
        )
    result.sort(key=lambda e: (-e["memory_count"], e["name"].lower()))
    return result


def find_org_key(orgs: list[dict], query: str) -> Optional[str]:
    """Resolve a free-text query to an org key: exact domain/key match, then
    a case-insensitive name/domain substring match (first, most-frequent) —
    mirrors ``people.find_person_key``."""
    q = query.strip().lower()
    if not q:
        return None
    for o in orgs:
        if o["key"] == q or o["domain"].lower() == q:
            return o["key"]
    for o in orgs:  # orgs is already sorted by frequency
        if q in o["name"].lower() or q in o["domain"].lower():
            return o["key"]
    return None
