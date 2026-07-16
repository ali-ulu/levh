"""Deterministic demo dataset for `stackmemory seed-demo`.

A small, self-consistent slice of a fictional engineer's work life — enough to
light up every surface of the dashboard (people, organizations, projects,
decisions, tasks, meeting-prep, briefing, the trust breakdown, the forgetting
curve, and one genuine conflict candidate) without any network or LLM call.

Nothing here is secret or real. Entities are driven by metadata (the same
`from` / `attendees` / `speakers` fields real connectors emit), so the seeded
data flows through the exact production extraction path — reindex, trust, and
conflict detection all see it as they would any imported memory.

`demo_memories()` returns plain dicts; the engine (`seed_demo`) is responsible
for backdating `created_at` from `age_days` and applying the reinforcement
hints. Keeping the data pure and declarative here makes it trivially testable.
"""

from __future__ import annotations

# Fictional cast — kept consistent so the entity graph and co-occurrence views
# look like a real team, not random noise.
_AYSE = "Ayşe Demir <ayse@northwind.co>"          # PM
_MERT = "Mert Kaya <mert@northwind.co>"           # backend lead
_ELIF = "Elif Şahin <elif@northwind.co>"          # design
_DENIZ = "Deniz Aydın <deniz@zephyrlabs.io>"      # client contact (Zephyr Labs)

# Reinforcement hints: (recall_count, stability_hours). A "strong" memory has
# been recalled many times so its half-life is long — it resists forgetting.
_STRONG = {"recall_count": 9, "stability_hours": 2400.0, "frequency": 10}
_FADING = {"recall_count": 0, "stability_hours": 72.0, "frequency": 1}


def demo_memories() -> list[dict]:
    """The demo corpus. Each entry is a kwargs-ish dict consumed by
    `MemoryEngine.seed_demo`. `age_days` backdates the memory; `reinforce`
    (optional) nudges its durability so the forgetting curve shows a real
    spread of strong vs fading memories."""
    return [
        # --- Decisions (high importance, some pinned) ------------------------
        {
            "content": (
                "We decided to use Postgres over MongoDB for Atlas — relational "
                "integrity matters more than raw write throughput for our workload."
            ),
            "importance": 0.9,
            "pinned": True,
            "project": "atlas",
            "source": "decision",
            "tags": ["decision", "architecture"],
            "age_days": 30,
            "metadata": {"attendees": [_AYSE, _MERT], "title": "Atlas datastore decision"},
        },
        {
            "content": (
                "Team agreed to ship the Beacon MVP by the end of Q1. Scope is "
                "locked to the three core dashboards — no new asks until it lands."
            ),
            "importance": 0.85,
            "project": "beacon",
            "source": "decision",
            "tags": ["decision", "planning"],
            "age_days": 21,
            "metadata": {"attendees": [_AYSE, _ELIF], "title": "Beacon MVP scope"},
        },
        {
            "content": (
                "Decided to drop the legacy SOAP integration next quarter — only two "
                "customers remain on it and both have a REST migration path."
            ),
            "importance": 0.75,
            "project": "atlas",
            "source": "decision",
            "tags": ["decision", "deprecation"],
            "age_days": 14,
            "metadata": {"attendees": [_MERT], "title": "Deprecate SOAP"},
        },
        # --- Meetings / events ----------------------------------------------
        {
            "content": (
                "Atlas weekly standup: the auth refactor is on track, Elif shared the "
                "new onboarding flow, Mert flagged a flaky test in the billing module."
            ),
            "importance": 0.5,
            "project": "atlas",
            "source": "calendar",
            "tags": ["standup"],
            "age_days": 3,
            "metadata": {"attendees": [_AYSE, _MERT, _ELIF], "title": "Atlas standup"},
        },
        {
            "content": (
                "Client sync with Zephyr Labs. Deniz wants SSO before the pilot "
                "rollout. Agreed to send an updated SOW by Friday."
            ),
            "importance": 0.7,
            "project": "atlas",
            "source": "transcript",
            "tags": ["client", "zephyr"],
            "age_days": 5,
            "metadata": {"speakers": [_DENIZ], "title": "Zephyr pilot sync"},
        },
        {
            "content": (
                "Design review with Elif: the dark-mode palette passes contrast "
                "checks. Cleared to ship in the next release."
            ),
            "importance": 0.45,
            "project": "atlas",
            "source": "calendar",
            "tags": ["design"],
            "age_days": 8,
            "metadata": {"attendees": [_ELIF], "title": "Design review"},
        },
        {
            "content": (
                "Kickoff for Project Beacon: internal analytics for the support team. "
                "Ayşe is PM, Elif on design, Mert on backend."
            ),
            "importance": 0.7,
            "project": "beacon",
            "source": "calendar",
            "tags": ["kickoff", "beacon"],
            "age_days": 28,
            "metadata": {"attendees": [_AYSE, _ELIF, _MERT], "title": "Beacon kickoff"},
        },
        {
            "content": (
                "Zephyr Labs pilot success criteria: SSO, 99.5% uptime, and CSV "
                "export. Deniz will sign off on the checklist."
            ),
            "importance": 0.7,
            "project": "atlas",
            "source": "transcript",
            "tags": ["client", "zephyr"],
            "age_days": 9,
            "metadata": {"speakers": [_DENIZ], "title": "Zephyr success criteria"},
        },
        # --- Emails ----------------------------------------------------------
        {
            "content": (
                "Mert: the auth PR is ready for review. Adds refresh-token rotation "
                "and rate limiting on the login endpoint."
            ),
            "importance": 0.6,
            "project": "atlas",
            "source": "email",
            "tags": ["review"],
            "age_days": 2,
            "metadata": {"from": _MERT},
        },
        {
            "content": (
                "Deniz from Zephyr Labs asked for the pilot timeline and pricing "
                "tiers. Needs an answer before their board meeting."
            ),
            "importance": 0.7,
            "project": "atlas",
            "source": "email",
            "tags": ["client", "zephyr"],
            "age_days": 4,
            "metadata": {"from": _DENIZ},
        },
        # --- Tasks (content triggers the task detector) ----------------------
        {
            "content": "I need to review Mert's auth PR before the Thursday release cut.",
            "importance": 0.65,
            "project": "atlas",
            "source": "manual",
            "tags": ["task"],
            "age_days": 1,
            "metadata": {},
        },
        {
            "content": "TODO: send Zephyr Labs the updated SOW with the SSO line item.",
            "importance": 0.7,
            "project": "atlas",
            "source": "manual",
            "tags": ["task", "client"],
            "age_days": 1,
            "metadata": {},
        },
        {
            "content": "I will prepare the Atlas demo environment for the Friday client call.",
            "importance": 0.6,
            "project": "atlas",
            "source": "manual",
            "tags": ["task"],
            "age_days": 2,
            "metadata": {},
        },
        # --- Conflict pair: same entity (Ayşe), opposing deadline value ------
        # Different source types (calendar vs email) raise the candidate's
        # review priority. This is a *candidate*, never a verdict.
        {
            "content": (
                "Per Ayşe, the Atlas deadline is 2026-03-15 — she wants the launch "
                "aligned with the marketing push."
            ),
            "importance": 0.6,
            "project": "atlas",
            "source": "calendar",
            "tags": ["timeline"],
            "age_days": 12,
            "metadata": {"attendees": [_AYSE], "title": "Atlas timeline"},
        },
        {
            "content": (
                "Update from planning: the Atlas deadline is 2026-04-02 now. The "
                "marketing push slipped two weeks."
            ),
            "importance": 0.6,
            "project": "atlas",
            "source": "email",
            "tags": ["timeline"],
            "age_days": 4,
            "metadata": {"from": _AYSE},
        },
        # --- Durable / reinforced (long half-life, strong retention) ---------
        {
            "content": (
                "Northwind on-call rotation: escalate Sev-1s to Mert first, then "
                "Ayşe. The runbook lives in the ops wiki."
            ),
            "importance": 0.8,
            "pinned": True,
            "project": "beacon",
            "source": "manual",
            "tags": ["ops", "runbook"],
            "age_days": 45,
            "metadata": {"attendees": [_MERT, _AYSE], "title": "On-call rotation"},
            "reinforce": _STRONG,
        },
        {
            "content": (
                "Atlas prod region is eu-central-1. Never run migrations during EU "
                "business hours."
            ),
            "importance": 0.85,
            "pinned": True,
            "project": "atlas",
            "source": "manual",
            "tags": ["ops"],
            "age_days": 25,
            "metadata": {},
            "reinforce": _STRONG,
        },
        # --- Low-importance chatter (fades — shows the forgetting gradient) ---
        {
            "content": (
                "Coffee chat with Elif — she's picking up Rust on the side, might "
                "prototype the Beacon ingest worker in it."
            ),
            "importance": 0.2,
            "project": "beacon",
            "source": "chat",
            "tags": ["chatter"],
            "age_days": 40,
            "metadata": {"attendees": [_ELIF]},
            "reinforce": _FADING,
        },
        {
            "content": (
                "Note to self: staging DB password rotation is handled by the "
                "platform team, not us."
            ),
            "importance": 0.3,
            "project": "atlas",
            "source": "manual",
            "tags": ["chatter"],
            "age_days": 35,
            "metadata": {},
            "reinforce": _FADING,
        },
        {
            "content": (
                "Mert mentioned the flaky billing test is a timezone issue in the "
                "fixtures. Low priority for now."
            ),
            "importance": 0.25,
            "project": "atlas",
            "source": "chat",
            "tags": ["chatter"],
            "age_days": 18,
            "metadata": {"attendees": [_MERT]},
            "reinforce": _FADING,
        },
    ]
