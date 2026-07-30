"""Tests for free-text people/organization extraction.

The point of this layer is that a hand-typed note produces entities at all.
The equally important half is that a repo import does *not* mint an entity per
capitalised identifier, so the negative cases below are load-bearing.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core.entities import extract_entities
from server.core.text_entities import (
    extract_orgs_from_text,
    extract_people_from_text,
    looks_like_code,
    people_in_memory,
)


class Mem:
    """Duck-typed stand-in for a Memory."""

    def __init__(self, content="", metadata=None, source="", mem_id="m1"):
        self.id = mem_id
        self.content = content
        self.metadata = metadata or {}
        self.source = source


# ── people from prose ────────────────────────────────────────────────


def test_email_in_content_yields_person():
    assert extract_people_from_text("Followed up with ali@acme.com today") == [
        ("ali", "ali@acme.com")
    ]


def test_mentions_yield_people():
    found = extract_people_from_text("Reviewed PR from @mert-dev and @aylin")
    assert ("mert-dev", "") in found
    assert ("aylin", "") in found


def test_anchored_english_phrasing_yields_person():
    assert ("Ayse", "") in extract_people_from_text("Met with Ayse about pricing")


def test_anchored_turkish_phrasing_yields_person():
    assert ("Deniz", "") in extract_people_from_text("Deniz ile görüştüm dün")
    # Turkish typed without diacritics still resolves.
    assert ("Elif", "") in extract_people_from_text("Elif ile gorustum")


def test_multiword_name_is_kept_whole():
    assert ("Ayşe Yılmaz", "") in extract_people_from_text("met with Ayşe Yılmaz yesterday")


def test_generic_nouns_are_not_people():
    assert extract_people_from_text("Met with the team about scope") == []
    assert extract_people_from_text("Call with Support tomorrow") == []


def test_company_is_not_a_person():
    """You meet *with* Zephyr Labs, but it is an organization."""
    people = extract_people_from_text("Met with Zephyr Labs about the SLA")
    assert people == []
    assert "Zephyr Labs" in extract_orgs_from_text("Met with Zephyr Labs about the SLA")


# ── organizations from prose ─────────────────────────────────────────


def test_company_suffix_names_yield_orgs():
    orgs = extract_orgs_from_text("Kickoff with Zephyr Labs and Contoso Technologies")
    assert "Zephyr Labs" in orgs
    assert "Contoso Technologies" in orgs


def test_org_name_keeps_its_suffix():
    assert extract_orgs_from_text("Acme Inc. signed the contract") == ["Acme Inc"]


# ── code must not produce prose entities ─────────────────────────────

CODE = """# Copyright (c) 2026 Acme Inc. All rights reserved.
class MemoryEngine:
    def __init__(self, Database Systems):
        import os
        return None
"""


def test_code_detected_by_extension():
    assert looks_like_code("local_files", {"extension": ".py"}) is True
    assert looks_like_code("local_files", {"extension": ".md"}) is False


def test_code_detected_by_path_when_extension_missing():
    assert looks_like_code("github", {"file_path": "src/main.ts"}) is True


def test_code_detected_by_content_shape():
    """A pasted snippet with no metadata at all is still recognised."""
    assert looks_like_code("", {}, CODE) is True
    assert looks_like_code("", {}, "Met with Ayse.\nAgreed on scope.\nShip Friday.") is False


def test_code_yields_no_prose_entities():
    for metadata in ({"extension": ".py"}, {}):
        assert extract_people_from_text(CODE, "local_files", metadata) == []
        assert extract_orgs_from_text(CODE, "local_files", metadata) == []


# ── wiring: metadata + text merge ────────────────────────────────────


def test_metadata_and_text_people_merge():
    mem = Mem(
        content="Recap sent to mert@northwind.co",
        metadata={"attendees": ["Ayse Kaya <ayse@acme.com>"]},
    )
    keys = {email or name for name, email in people_in_memory(mem)}
    assert "ayse@acme.com" in keys  # from metadata
    assert "mert@northwind.co" in keys  # from content


def test_metadata_wins_on_duplicate_identity():
    """The structural field carries a role, so it must not be displaced."""
    mem = Mem(
        content="ayse@acme.com confirmed",
        metadata={"attendees": ["Ayse Kaya <ayse@acme.com>"]},
    )
    found = [p for p in people_in_memory(mem) if p[1] == "ayse@acme.com"]
    assert found == [("Ayse Kaya", "ayse@acme.com")]


def test_plain_note_now_produces_graph_entities():
    """The regression this layer exists for: a hand-typed note used to yield
    no person/organization entities at all."""
    mem = Mem(content="Met with Ayse from Zephyr Labs about ali@acme.com's proposal")
    entities = extract_entities(mem)
    types = {e["type"] for e in entities}
    assert "person" in types
    assert "organization" in types


def test_prose_org_folds_into_email_domain_in_same_memory():
    """One company, one node — the domain is the stabler identity."""
    mem = Mem(content="Emailed deniz@zephyrlabs.io about the Zephyr Labs SOW")
    org_keys = {e["key"] for e in extract_entities(mem) if e["type"] == "organization"}
    assert org_keys == {"zephyrlabs.io"}
