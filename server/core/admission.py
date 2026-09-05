"""Memory Admission Gate — decide what happens to an incoming memory BEFORE
it is stored, so growing the number of capture sources doesn't grow the noise.

Deterministic and offline (no LLM). Every candidate gets one of four verdicts:

  - ``reject``  — don't store: empty/too-short, or a near-exact duplicate of
                  something already remembered.
  - ``review``  — hold for a human: a near-duplicate (probably redundant) that
                  isn't identical enough to auto-reject.
  - ``redact``  — store, but strip secrets first (API keys, passwords, private
                  keys, tokens). Normal emails are NOT secrets — they feed the
                  people graph — so they're left intact.
  - ``admit``   — store as-is.

Precedence: reject > review > redact > admit. The duplicate signal
(``max_similarity``) is computed by the engine from the vector store and passed
in, keeping this module pure and trivially testable.
"""

from __future__ import annotations

import re
from typing import Any

# Value-bearing secret assignments: keep the label, redact the value.
#
# The label may carry an env-var style prefix (OPENAI_API_KEY, GH_TOKEN): an
# underscore is a word character, so a bare \b in front of the keyword never
# matches those — which is precisely the form a secret takes when it is pasted
# out of a shell or a .env file.
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b((?:[a-z0-9]+[_-])*"
    r"(?:password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?key|"
    r"secret[_-]?key|auth[_-]?token|token|client[_-]?secret))\b"
    r"(\s*[:=]\s*)"
    r"(\"[^\"]+\"|'[^']+'|\S+)"
)

# Standalone secret tokens (no label needed).
_STANDALONE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{16,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
]

_REDACTION = "[REDACTED]"


def redact_secrets(content: str) -> tuple[str, list[str]]:
    """Return ``(redacted_content, secret_types_found)``. Non-destructive when
    nothing matches (returns the original string and an empty list)."""
    if not content:
        return content, []

    found: list[str] = []
    redacted = content

    def _assign_sub(m: "re.Match") -> str:
        # Idempotency: an already-redacted value must not re-trigger a match,
        # so redacting the same content twice is a no-op the second time.
        if m.group(3) == _REDACTION:
            return m.group(0)
        found.append("credential_assignment")
        return f"{m.group(1)}{m.group(2)}{_REDACTION}"

    redacted = _ASSIGNMENT_RE.sub(_assign_sub, redacted)

    for name, pattern in _STANDALONE_PATTERNS:
        if pattern.search(redacted):
            found.append(name)
            redacted = pattern.sub(_REDACTION, redacted)

    # Stable, de-duplicated order.
    seen: set[str] = set()
    uniq = [f for f in found if not (f in seen or seen.add(f))]
    return redacted, uniq


def evaluate(
    content: str,
    max_similarity: float = 0.0,
    min_length: int = 3,
    dup_reject: float = 0.98,
    dup_review: float = 0.90,
) -> dict[str, Any]:
    """Decide what to do with a candidate memory. Pure — the caller supplies
    ``max_similarity`` (the highest cosine similarity to any existing memory).

    Returns a dict:
        action:            "reject" | "review" | "redact" | "admit"
        reasons:           list[str] (human-readable)
        reason_codes:      list[str] (machine-readable: too_short |
                           duplicate_exact | duplicate_near |
                           secrets_redacted | admitted)
        redacted_content:  content with any secrets stripped
        redacted:          bool (were secrets found)
        secrets:           list[str] (secret types found)
        max_similarity:    echoed back, rounded
    """
    reasons: list[str] = []
    text = (content or "").strip()

    redacted_content, secrets = redact_secrets(content or "")

    # 1) reject — empty / too short
    if len(text) < max(min_length, 1):
        reasons.append(f"content too short (<{min_length} chars)")
        return _result("reject", reasons, ["too_short"], redacted_content, secrets, max_similarity)

    # 2) reject — near-exact duplicate
    if max_similarity >= dup_reject:
        reasons.append(f"near-exact duplicate (similarity {round(max_similarity, 3)})")
        return _result("reject", reasons, ["duplicate_exact"], redacted_content, secrets, max_similarity)

    # 3) review — near-duplicate (redundant but not identical)
    if max_similarity >= dup_review:
        reasons.append(f"possible duplicate (similarity {round(max_similarity, 3)})")
        return _result("review", reasons, ["duplicate_near"], redacted_content, secrets, max_similarity)

    # 4) redact — secrets present, otherwise admissible
    if secrets:
        reasons.append(f"secrets redacted: {', '.join(secrets)}")
        return _result("redact", reasons, ["secrets_redacted"], redacted_content, secrets, max_similarity)

    # 5) admit
    reasons.append("admitted")
    return _result("admit", reasons, ["admitted"], redacted_content, secrets, max_similarity)


def _result(
    action: str,
    reasons: list[str],
    reason_codes: list[str],
    redacted_content: str,
    secrets: list[str],
    max_similarity: float,
) -> dict[str, Any]:
    return {
        "action": action,
        "reasons": reasons,
        "reason_codes": reason_codes,
        "redacted_content": redacted_content,
        "redacted": bool(secrets),
        "secrets": secrets,
        "max_similarity": round(max_similarity, 4),
    }
