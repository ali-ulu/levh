"""Where LEVH decides whether a feature may talk to a remote LLM.

Every outbound answer/summary path funnels through here, so "does this send my
memories to OpenAI?" has exactly one answer in exactly one place.

The rule mirrors the embedder's long-standing policy (see ``embedder.py``):
``auto`` is **local-first and never selects a remote provider merely because a
credential happens to exist in the environment**. A developer machine almost
always has ``OPENAI_API_KEY`` exported for something else entirely; treating
that as consent to upload a personal memory store is not a defensible default
for a local-first tool.

Turning it on is one explicit environment variable per feature:

    ANSWER_MODE=llm     # ask / answer synthesis may call OpenAI
    SUMMARY_MODE=llm    # session summaries, consolidation, transcript ingest

Unset (the default) means every one of those paths runs its deterministic
offline fallback and performs no network I/O at all.
"""

from __future__ import annotations

import os

ANSWER_FEATURE = "answer"
SUMMARY_FEATURE = "summary"

#: Env var holding the operator's opt-in for each feature.
FEATURE_ENV_VARS = {
    ANSWER_FEATURE: "ANSWER_MODE",
    SUMMARY_FEATURE: "SUMMARY_MODE",
}

#: Values that mean "yes, this feature may make outbound LLM calls".
_LLM_OPT_IN = {"llm", "openai", "remote", "on", "true", "1", "yes"}

LLM = "llm"
EXTRACTIVE = "extractive"


def _feature_opt_in(feature: str) -> bool:
    """Has the operator explicitly enabled remote LLM calls for ``feature``?"""
    env_var = FEATURE_ENV_VARS.get(feature)
    if not env_var:
        return False
    return os.getenv(env_var, "").strip().lower() in _LLM_OPT_IN


def has_credential() -> bool:
    """Is there an OpenAI credential to call with at all?"""
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def resolve_mode(requested: str, feature: str) -> str:
    """Resolve a requested mode to the backend that will actually run.

    ``requested`` is ``"auto"`` at every call site in the product; the explicit
    values exist for library callers and tests.

      - ``"extractive"`` → always the offline fallback.
      - ``"llm"``        → an explicit caller-level request; still needs a
                           credential, otherwise it degrades to offline rather
                           than failing a user's session end.
      - ``"auto"``       → offline **unless** the feature's env opt-in is set.
                           An ambient credential alone is never enough.
    """
    if requested == EXTRACTIVE:
        return EXTRACTIVE
    if requested == LLM:
        return LLM if has_credential() else EXTRACTIVE
    # "auto" and anything unrecognised: local-first.
    if _feature_opt_in(feature) and has_credential():
        return LLM
    return EXTRACTIVE


def use_llm(requested: str, feature: str) -> bool:
    """Convenience predicate over :func:`resolve_mode`."""
    return resolve_mode(requested, feature) == LLM


def outbound_status() -> dict:
    """Report the effective outbound posture, for `/api/config` and Settings.

    Deliberately reports whether a credential is *present*, never its value.
    """
    return {
        "answer_backend": resolve_mode("auto", ANSWER_FEATURE),
        "summary_backend": resolve_mode("auto", SUMMARY_FEATURE),
        "openai_credential_present": has_credential(),
        "outbound_llm_enabled": (
            use_llm("auto", ANSWER_FEATURE) or use_llm("auto", SUMMARY_FEATURE)
        ),
    }
