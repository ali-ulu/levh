# Commercial surfaces: the decision rule and what each one costs (#298)

Tarih: 2026-10-02 · Durum: karar verildi · Tür: tasarım kararı

Design issue #298 asked where the boundary sits between the four candidate
commercial surfaces. The rule that answers it is recorded here so the decision
is a fact in the tree, not something reconstructed from an issue thread. It is
internal: a dated revenue plan reads as a product claim when it reaches the
published docs.

## The rule: does the surface need a second principal?

One test resolves the boundary the issue left open:

- **No principal** — support, SLA, a signed build. No engine change. Decidable
  now.
- **One principal** — hosted sync for a single user. Needs auth beyond
  `LEVH_TOKEN`, but **no tenancy**. Decidable now, sequenced after the first.
- **Many principals** — team workspace. This is where the `AGENTS.md` invariant
  bites: all four transports resolve the engine through
  `server/core/engine_provider.get_engine()` and must never construct a second
  one. Tenancy is therefore either one engine with a workspace dimension
  threaded through every store/recall path, or one engine *per workspace* — and
  the second collides with the invariant's stated reason (it splits the
  short-term deque and the vector store). That scoping question is #302's, not
  #298's.
- **SSO / metering** — attaches to the team workspace; not a product on its own.

## The decision

Adopt **support/SLA/signed build** now, approve **single-user hosted sync** as
the first funded surface, and route everything that needs a second principal to
#302. #302 has since landed phase 1 (the tenancy boundary as the degenerate
single-user case) and closed; its phases 2–4 remain gated on the triggers in
[`SHARED-MEMORY-DESIGN.md`](SHARED-MEMORY-DESIGN.md), so the team workspace and
SSO/metering are gated on those triggers rather than on an open design question.

## What the first surface costs (it is small — that is the argument for it)

- The supply-chain half already exists: `pip-audit`, `bandit`, CodeQL and a
  CycloneDX SBOM job run in CI, and `scripts/release.py` makes releases
  reproducible. A signed attestation over the wheel and the SBOM is packaging
  work, not product work — CI already produces everything it would sign.
- The SLA half needs a published compatibility matrix (Python 3.11–3.13 and the
  tested SQLite version are already the CI matrix) and a support-channel
  decision. Zero runtime code, zero invariant contact.
- Licence-wise it is uncontroversial: selling support on an AGPL product needs
  no dual-license footing and no contributor sign-off, unlike a proprietary
  tier, which must wait for the DCO change (#296) to reach later contributions.

## What the second surface needs that the tree does not have yet

1. **Identity beyond the single token.** `LEVH_TOKEN` is all-or-nothing and
   identifies no user; a per-user relay needs at least "whose store is this" —
   the smallest slice of #302's identity work, doable without the full model.
2. **A recorded key-handling threat model.** `server/core/backup.py` already
   ships passphrase-encrypted backup, so the relay can build on that primitive
   and plausibly stay zero-knowledge. This has to be written down before any
   hosting decision, because it decides whether the relay is a feature or a
   liability.
3. **A conflict story for concurrent writers.** One user, several machines, one
   hosted SQLite file is the concurrent-writer case SQLite is worst at. The
   honest options are a single-writer relay (serialize) or the Postgres backend
   the issue rejects for now. The single-writer limit should be stated as a
   product constraint of this surface, not hidden as an implementation detail.

## The four questions #298 left to the maintainer

1. **Is (1) alone an acceptable first revenue step, or must (2) be scoped in
   the same decision?** Recorded answer: start with (1); (2) is approved in
   principle and scoped only when its three prerequisites above exist. This
   keeps the first chargeable surface free of runtime risk.
2. **For (2): zero-knowledge relay versus managed keys?** Recorded answer:
   zero-knowledge. It builds on `backup.py`'s existing primitive and is the only
   option that does not contradict the README's "memory does not leave the
   machine" thesis.
3. **Is a hosted surface acceptable positioning-wise at all, or must (2) ship
   as a self-hostable sync server the project publishes but does not operate?**
   Recorded answer: publish a self-hostable sync server and charge for
   operation. The thesis is preserved and the product still has a revenue path.
4. **Confirm the sequencing: merge nothing from (3)/(4) until #302 lands.**
   Resolved: #302 landed its phase 1 and closed. (3)/(4) now wait on the phase
   2–4 triggers in `SHARED-MEMORY-DESIGN.md`, not on an open decision.

## Not decided here

These are commercial/product choices that ultimately belong to the owner, not
to an engineering decision record: pricing, the support channel, and whether to
operate a hosted service at all. The rule and the defaults above are what the
engineering side can fix without them; the rest is a business call.

## See also

- `docs/internal/ROADMAP.md` item B and the `multi-user-postgres` workstream.
- #309 — the concrete feature list each revenue path requires.
