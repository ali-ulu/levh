"""Query-term expansion — teach recall the words a user might use instead.

Model-free ranking matches the words of the query against the words of a
memory (:mod:`server.core.lexical`). That is exactly right for inflection —
"migrasyon" finds "migrasyonu" — and exactly wrong for synonymy: a memory
about "API authentication uses JWT tokens" shares no surface word with "how do
users log in", so it can never become a candidate however well it answers the
question. Measured before this module existed: that query returned an unrelated
memory first and the JWT memory lower, on the candidate list only because the
corpus was tiny.

This closes the gap without a model. A query term is expanded with the terms
the store considers equivalent, and the expansions take part in candidate
retrieval and in the lexical score. Sources are merged:

* a small built-in map for equivalences that cost nothing to know, and
* a user file (``LEVH_SYNONYMS_PATH``) for the vocabulary of a particular
  store — the data file shipped with the package is the common case.

The map is deliberately not applied to admission's duplicate check: two
memories that merely use different words for the same idea ("login" and
"signin") are not duplicates, and widening that comparison would start
dropping real memories. Expansion belongs to retrieval, where over-matching
costs a little precision instead of losing data.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from .env import get_env

logger = logging.getLogger("levh.synonyms")

SYNONYMS_ENV = "SYNONYMS_PATH"

_TOKEN = re.compile(r"\w+", flags=re.UNICODE)

# Equivalences worth knowing with no file present: the words that most often
# split a question from its answer in a developer's memory store. Kept small —
# a large map belongs in the data file, where it can be reviewed as data.
_BUILTIN: Mapping[str, tuple[str, ...]] = {
    "login": ("signin", "authentication", "auth"),
    "signin": ("login", "authentication", "auth"),
    "logout": ("signout",),
    "signout": ("logout",),
    "auth": ("authentication", "login", "authorization"),
    "config": ("configuration", "settings"),
    "configuration": ("config", "settings"),
    "settings": ("config", "configuration"),
    "deploy": ("deployment", "release", "ship"),
    "deployment": ("deploy", "release"),
    "db": ("database", "postgres", "sql"),
    "database": ("db", "postgres", "sql"),
    "migrate": ("migration", "migrasyon"),
    "migration": ("migrate", "migrasyon"),
    "token": ("tokens", "jwt"),
    "jwt": ("token", "auth"),
}

#: ``term -> equivalents`` for the loaded user file, keyed by absolute path so
#: two stores in one process do not shadow each other. Refreshed when mtime
#: changes, the way a config file is expected to behave.
_FILE_CACHE: dict[str, tuple[float, dict[str, frozenset[str]]]] = {}


def _normalize(term: str) -> str:
    return (term or "").strip().lower()


def _components(pairs: Iterable[tuple[str, str]]) -> dict[str, frozenset[str]]:
    """Undirected closure of the equivalences.

    An equivalence is symmetric and transitive: if ``a~b`` and ``b~c`` then all
    three are one group, so a query for ``a`` also reaches ``c``. Without the
    closure a file listing only pairwise edges would silently stop at the first
    hop.
    """
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for left, right in pairs:
        union(left, right)

    groups: dict[str, set[str]] = {}
    for node in parent:
        groups.setdefault(find(node), set()).add(node)
    return {
        node: frozenset(members - {node})
        for node, members in ((n, groups[find(n)]) for n in parent)
    }


def parse_synonyms(raw: object) -> dict[str, frozenset[str]]:
    """Read a synonyms payload into ``term -> equivalents``.

    Accepts three shapes, because each is a natural way to write the file and
    none is worth rejecting:

    * ``{"login": ["signin", "auth"]}`` — a map of term to equivalents,
    * ``[["login", "signin", "auth"]]`` — a list of groups, or
    * ``{"groups": [[...]]}`` — a list of groups under a documented key, which
      is what the shipped data file uses so it can carry an explanatory
      ``_comment`` beside it.

    Each entry is matched to every other in its group. Unusable entries are
    skipped, not fatal: a malformed line should cost that line, not recall.
    """
    if isinstance(raw, Mapping) and "groups" in raw:
        return parse_synonyms(raw["groups"])
    pairs: list[tuple[str, str]] = []
    if isinstance(raw, Mapping):
        for key, values in raw.items():
            left = _normalize(key) if isinstance(key, str) else ""
            if not left or not isinstance(values, (list, tuple)):
                continue
            for value in values:
                right = _normalize(value) if isinstance(value, str) else ""
                if right and right != left:
                    pairs.append((left, right))
    elif isinstance(raw, (list, tuple)):
        for group in raw:
            if not isinstance(group, (list, tuple)):
                continue
            members = [_normalize(m) for m in group if isinstance(m, str)]
            members = [m for m in members if m]
            for left in members:
                for right in members:
                    if left != right:
                        pairs.append((left, right))
    return _components(pairs)


def load_file(path: str | os.PathLike[str] | None) -> dict[str, frozenset[str]]:
    """Load and cache a synonyms file; ``{}`` when there is nothing to load.

    A missing or malformed file is logged once and yields no expansions rather
    than raising: recall must not fail because a convenience file is wrong.
    """
    if not path:
        return {}
    resolved = str(Path(path).expanduser())
    try:
        mtime = os.path.getmtime(resolved)
    except OSError:
        _FILE_CACHE.pop(resolved, None)
        return {}
    cached = _FILE_CACHE.get(resolved)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        raw = json.loads(Path(resolved).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("ignoring synonyms file %s: %s", resolved, exc)
        _FILE_CACHE[resolved] = (mtime, {})
        return {}
    parsed = parse_synonyms(raw)
    _FILE_CACHE[resolved] = (mtime, parsed)
    return parsed


def configured_path() -> str | None:
    """The synonyms file to read: ``LEVH_SYNONYMS_PATH`` when set.

    Unset means the vocabulary shipped with the package, so expansion works
    out of the box; set it to point at a store's own file instead.
    """
    value = get_env(SYNONYMS_ENV, "") or ""
    if value.strip():
        return value.strip()
    return default_path()


def default_path() -> str | None:
    """The packaged vocabulary, if it is present in this install.

    Resolved relative to this module so it works from a wheel, a source
    checkout and a frozen bundle alike. A missing file is not an error — the
    built-in map still applies.
    """
    candidate = Path(__file__).resolve().parents[1] / "data" / "synonyms.json"
    return str(candidate) if candidate.is_file() else None


class SynonymTable:
    """Merged view of the built-in map and one user file.

    Built per recall from the configured path; the file parse is cached by
    mtime, so the steady-state cost is a dict lookup and one ``stat``.
    """

    __slots__ = ("_table", "_phrases")

    def __init__(
        self,
        table: Mapping[str, frozenset[str]],
        phrases: Mapping[str, frozenset[str]] | None = None,
    ) -> None:
        self._table = table
        self._phrases = dict(phrases or {})

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> "SynonymTable":
        merged: dict[str, set[str]] = {}
        for term, equivalents in _BUILTIN.items():
            merged.setdefault(_normalize(term), set()).update(
                _normalize(e) for e in equivalents
            )
        file_entries = load_file(path or configured_path())
        for file_term, file_equivalents in file_entries.items():
            merged.setdefault(file_term, set()).update(file_equivalents)
        # Re-run the closure: the file can connect two built-in groups.
        pairs = [
            (term, equivalent)
            for term, equivalents in merged.items()
            for equivalent in equivalents
        ]
        table = _components(pairs)

        # Multi-word members need separate handling: "log in" is one idea, but
        # the tokenizer sees "log" and "in", so a table keyed on the phrase is
        # never looked up. For each group, remember which phrase keys share a
        # group and which single words that group holds, so a phrase found in
        # the query can lend the group's words to the query's own tokens.
        phrases: dict[str, frozenset[str]] = {}
        for members in _file_groups(path or configured_path()):
            words = frozenset(m for m in members if " " not in m)
            for member in members:
                if " " in member:
                    phrases[member] = words
        return cls(table, phrases)

    def for_term(self, term: str) -> frozenset[str]:
        return self._table.get(_normalize(term), frozenset())

    def expand(self, query: str) -> dict[str, frozenset[str]]:
        """``query term -> equivalents`` for the terms that have any.

        Keyed per query term, not one flat set: the lexical score counts a
        query term as covered when *its own* equivalents appear in the content,
        so flattening would let an unrelated expansion inflate the match.
        """
        from .lexical import terms as _terms

        query_terms = _terms(query)
        collected: dict[str, set[str]] = {
            term: set(self.for_term(term))
            for term in query_terms
            if self.for_term(term)
        }
        if self._phrases:
            # Order matters: a phrase only matches if its words appear adjacent,
            # so the token stream is rebuilt in reading order rather than from
            # a set. Stopwords are kept precisely so "log in" stays contiguous.
            tokens = _TOKEN.findall((query or "").lower())
            normalized = " " + " ".join(tokens) + " "
            for phrase, words in self._phrases.items():
                if f" {phrase} " not in normalized:
                    continue
                for token in phrase.split():
                    if token in query_terms:
                        collected.setdefault(token, set()).update(words)
        return {term: frozenset(value) for term, value in collected.items() if value}


def _file_groups(path: str | os.PathLike[str] | None) -> list[list[str]]:
    """The file's groups as written, before the closure flattens them."""
    if not path:
        return []
    resolved = Path(str(Path(path).expanduser()))
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(raw, Mapping) and "groups" in raw:
        raw = raw["groups"]
    groups: list[list[str]] = []
    if isinstance(raw, Mapping):
        for key, values in raw.items():
            members = [_normalize(key)]
            if isinstance(values, (list, tuple)):
                members += [_normalize(v) for v in values if isinstance(v, str)]
            groups.append([m for m in members if m])
    elif isinstance(raw, (list, tuple)):
        for group in raw:
            if isinstance(group, (list, tuple)):
                groups.append([_normalize(m) for m in group if isinstance(m, str)])
    return [[m for m in group if m] for group in groups]
