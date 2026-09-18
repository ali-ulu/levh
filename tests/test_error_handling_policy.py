"""`except Exception: pass` must not exist in `server/` (issue #220).

`docs/error-handling.md` states the rule plainly: catching everything is
allowed only when the failure is *not silently lost*. Six sites kept a body
that was nothing but `pass` and a `# noqa` explaining why the failure was
tolerated — which says nothing about the failure being *recorded*. A lint
rule cannot see this: `# noqa: BLE001` silences ruff, so the swallow has to
be caught by reading the tree instead.

This guard parses `server/` and rejects any `except Exception` handler whose
body is only `pass`/`...`/a docstring. It does not require a specific
mechanism: logging, returning a visible result, raising, or assigning to an
error counter all count, exactly as the policy's second rule allows. The
same check bans a bare `except:` or `except BaseException:`, which would
also swallow `KeyboardInterrupt` and `SystemExit`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1] / "server"

_BROAD = {"Exception", "BaseException"}


def _broad_handler_name(handler: ast.ExceptHandler) -> str | None:
    """The name a handler catches if it is broad, else None."""
    if handler.type is None:
        return "<bare>"  # `except:` catches everything, including KeyboardInterrupt
    name = ast.unparse(handler.type)
    return name if name in _BROAD else None


def _is_swallow(handler: ast.ExceptHandler) -> bool:
    """True when the handler's whole body is `pass`, `...`, or a docstring."""
    body = [
        stmt
        for stmt in handler.body
        if not (
            isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)
        )
    ]
    if not body:
        return True  # a docstring-only body records nothing either
    return all(
        isinstance(stmt, ast.Pass)
        or (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis)
        for stmt in body
    )


def _swallow_sites() -> list[str]:
    sites: list[str] = []
    for path in sorted(SERVER.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _broad_handler_name(node) and _is_swallow(node):
                rel = path.relative_to(SERVER.parent)
                sites.append(f"{rel}:{node.lineno} ({_broad_handler_name(node)})")
    return sites


def test_no_silently_swallowed_broad_exception_in_server():
    sites = _swallow_sites()
    assert not sites, (
        "docs/error-handling.md: `except Exception: pass` is never acceptable. "
        "Log the failure (`logger.exception`), return a visible result, or count "
        "it as an error — and keep the one-line `# noqa: BLE001 - <reason>`. "
        "A `# noqa` only quiets ruff, not this guard. Offending sites:\n  " + "\n  ".join(sites)
    )


def test_guard_actually_detects_the_pattern(tmp_path: Path):
    """A guard that cannot fail is decoration; prove it flags a real swallow."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "def f():\n    try:\n        g()\n    except Exception:  # noqa: BLE001 - tolerated\n        pass\n",
        encoding="utf-8",
    )
    tree = ast.parse(sample.read_text(encoding="utf-8"))
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler))
    assert _broad_handler_name(handler) == "Exception"
    assert _is_swallow(handler), "the detector must flag a `pass`-only handler"


@pytest.mark.parametrize(
    "body",
    [
        "logger.exception('boom')",
        "return None",
        "raise",
        "self._errors += 1",
        "print('failed')",
    ],
    ids=["logs", "returns", "reraises", "counts", "reports"],
)
def test_visible_handling_mechanisms_are_not_flagged(body: str):
    """Every documented alternative to logging must pass the guard."""
    tree = ast.parse(f"def f():\n    try:\n        g()\n    except Exception:\n        {body}\n")
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler))
    assert not _is_swallow(handler)
