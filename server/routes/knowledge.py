"""Knowledge routes — projects, people, organizations, decisions, timeline."""

from __future__ import annotations


from fastapi import APIRouter, HTTPException

from server.routes.deps import get_engine

router = APIRouter()


@router.get("/api/projects")
async def list_projects():
    engine = await get_engine()
    return {"projects": await engine.list_projects()}


@router.get("/api/sources")
async def list_sources():
    engine = await get_engine()
    return {"sources": await engine.list_sources()}


@router.get("/api/tags")
async def list_tags():
    engine = await get_engine()
    return {"tags": await engine.list_tags()}


@router.get("/api/people")
async def list_people(limit: int = 200):
    """Distinct people across all memories (calendar attendees, email
    senders/recipients, transcript speakers), most-frequent first."""
    engine = await get_engine()
    return {"people": await engine.list_people(limit=min(max(limit, 1), 1000))}


@router.get("/api/people/{key:path}")
async def get_person(key: str):
    """A person's profile plus every memory that mentions them. ``key`` may be
    an email, a person key, or a free-text name (resolved by best match)."""
    engine = await get_engine()
    person = await engine.get_person(key)
    if person is None:
        raise HTTPException(status_code=404, detail="person not found")
    return person


@router.get("/api/timeline")
async def get_timeline(days: int = 30, project: str = ""):
    """Episodic memories grouped by day, most recent first — "what happened
    this/last week"."""
    engine = await get_engine()
    return {"timeline": await engine.timeline(days=min(max(days, 1), 365), project=project or None)}


@router.get("/api/briefing")
async def get_briefing(days: int = 7, project: str = ""):
    """Deterministic Daily Briefing — what's on today, open commitments from
    recent memories, and memories that are fading and may need review."""
    engine = await get_engine()
    return {"briefing": await engine.briefing(days=min(max(days, 1), 90), project=project or None)}


@router.get("/api/meeting-prep")
async def get_meeting_prep(query: str = "", within_days: int = 14):
    """Proactive pre-meeting brief — the next upcoming meeting (or a matched
    one), each attendee's recent context, and relevant open commitments and
    decisions. Deterministic, offline."""
    engine = await get_engine()
    return {
        "meeting_prep": await engine.meeting_prep(
            query=query or "", within_days=min(max(within_days, 1), 90)
        )
    }


@router.get("/api/organizations")
async def list_organizations(limit: int = 200):
    """Distinct organizations across all memories (people grouped by email
    domain), most-frequent first."""
    engine = await get_engine()
    return {"organizations": await engine.list_organizations(limit=min(max(limit, 1), 1000))}


@router.get("/api/organizations/{key:path}")
async def get_organization(key: str):
    """An organization's profile plus every memory that mentions someone from
    it. ``key`` may be a domain or a free-text name (resolved by best match)."""
    engine = await get_engine()
    org = await engine.get_organization(key)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")
    return org


@router.get("/api/decisions")
async def list_decisions(days: int = 90, project: str = "", limit: int = 50):
    """Deterministic decision detection — statements like "we decided" /
    "agreed to" / "karar verdik" in recent episodic memory content."""
    engine = await get_engine()
    return {
        "decisions": await engine.list_decisions(
            days=min(max(days, 1), 365), project=project or None, limit=min(max(limit, 1), 200)
        )
    }
