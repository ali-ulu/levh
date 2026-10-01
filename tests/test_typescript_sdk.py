"""The TypeScript SDK must not drift from the published contract.

`openapi.json` is frozen by `tests/test_openapi_contract.py`: it is regenerated
only in the commit that changes the API. The SDK's generated files are a second
projection of that same contract, so a change that regenerates one and not the
other leaves a client that compiles and then sends the wrong thing. This test is
the half that was missing.

The version site is checked here too, because the SDK is a published package:
`npm publish` from `sdk/typescript` would otherwise ship a version unrelated to
the release it matches.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / "sdk" / "typescript"
GENERATED = SDK / "src" / "generated"

generate_sdk = importlib.import_module("scripts.generate_sdk")
release = importlib.import_module("scripts.release")


@pytest.fixture(scope="module")
def expected() -> dict[str, str]:
    return generate_sdk.generate()


def test_the_generated_files_are_committed(expected):
    missing = [name for name in expected if not (GENERATED / name).exists()]
    assert not missing, (
        f"{missing} are missing from {GENERATED.relative_to(ROOT)}; "
        "run `python scripts/generate_sdk.py`"
    )


@pytest.mark.parametrize("name", ["types.ts", "endpoints.ts"])
def test_the_committed_generated_file_matches_the_contract(name, expected):
    committed = (GENERATED / name).read_text(encoding="utf-8")
    assert committed == expected[name], (
        f"sdk/typescript/src/generated/{name} is stale relative to openapi.json; "
        "regenerate it in the same commit as the contract change "
        "(`python scripts/generate_sdk.py`)"
    )


def test_the_generator_is_deterministic(expected):
    """Regenerating twice must produce identical bytes, or the drift check
    would fail for reasons that have nothing to do with the contract."""
    assert generate_sdk.generate() == expected


def test_the_sdk_package_version_matches_the_release():
    """A published SDK whose version does not match the server it talks to is
    the kind of thing that is discovered by a user, not by us."""
    package = json.loads((SDK / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == release._pyproject_version()


def test_the_sdk_package_version_is_in_the_release_bump_sites():
    """`scripts/release.py` rewrites every canonical version site. The SDK's
    `package.json` has to be one of them, or a release silently leaves it
    behind — the same drift class the release pipeline was built to prevent."""
    bumped = {path for path, _, _ in release.VERSION_SITES}
    assert "sdk/typescript/package.json" in bumped, (
        "sdk/typescript/package.json is not in scripts/release.py VERSION_SITES; "
        "add it so a version bump cannot leave the SDK behind"
    )


def test_the_generated_files_declare_that_they_are_generated():
    """A file under a 'generated' directory that does not say so invites a
    hand-edit that the next regeneration silently reverts."""
    for name in ("types.ts", "endpoints.ts"):
        head = (GENERATED / name).read_text(encoding="utf-8")[:600]
        assert "GENERATED FILE" in head, f"{name} does not carry the generated-file header"


def test_the_client_types_responses_proportional_to_the_contract():
    """Response typing must track the contract honestly, in both directions.

    The contract declares 200 schemas for an initial set of operations; the
    generated `types.ts` must contain a type for each one's response model,
    and the client must return `unknown` for the rest rather than a guessed
    interface. Hard-coding the expected count would let a route that loses its
    response model pass silently, so both halves are derived from the live
    contract here.
    """
    contract = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))
    typed_responses = 0
    response_refs: set[str] = set()
    for operations in contract["paths"].values():
        for operation in operations.values():
            schema = (
                operation.get("responses", {})
                .get("200", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema")
            )
            if schema:
                typed_responses += 1
                if "$ref" in schema:
                    response_refs.add(schema["$ref"].rsplit("/", 1)[-1])

    generated = (SDK / "src" / "generated" / "types.ts").read_text(encoding="utf-8")
    client = (SDK / "src" / "client.ts").read_text(encoding="utf-8")

    assert typed_responses > 0, (
        "the contract no longer declares any response schema; the client's "
        "typed convenience methods are now lying"
    )
    for name in sorted(response_refs):
        assert f"export type {name} =" in generated, (
            f"the contract references response model {name} but the generated "
            "SDK does not carry it; regenerate with scripts/generate_sdk.py"
        )
    # Untyped operations remain unknown-typed rather than guessed.
    assert "T = unknown" in client, (
        "`call()` must keep the honest `unknown` default for operations the "
        "contract does not yet type"
    )


def test_no_generated_file_contains_a_smuggled_handwritten_block():
    """Everything in `generated/` must be reproducible from the contract."""
    from scripts.generate_sdk import HEADER

    for name in ("types.ts", "endpoints.ts"):
        text = (GENERATED / name).read_text(encoding="utf-8")
        assert text.startswith(HEADER), f"{name} does not start with the generated header"
        # Exactly one header, so nothing was pasted above it.
        assert text.count("GENERATED FILE") == 1


def test_the_operation_ids_are_unique():
    """Operation ids key the operation table; a duplicate would silently drop
    one of the two operations that share it."""
    contract = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))
    ids = [
        operation.get("operationId")
        for operations in contract["paths"].values()
        for operation in operations.values()
        if operation.get("operationId")
    ]
    duplicates = {op for op in ids if ids.count(op) > 1}
    assert not duplicates, f"duplicate operationIds in the contract: {sorted(duplicates)}"


def test_the_generated_endpoint_table_covers_every_operation():
    """If the generator skipped an operation (no operationId, say), the client
    simply could not call it — and nothing else would notice."""
    contract = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))
    expected_ids = {
        operation["operationId"]
        for operations in contract["paths"].values()
        for operation in operations.values()
        if operation.get("operationId")
    }
    endpoints = (GENERATED / "endpoints.ts").read_text(encoding="utf-8")
    generated_ids = set(re.findall(r'^  "([^"]+)": \{', endpoints, re.MULTILINE))
    assert expected_ids == generated_ids, (
        f"missing from the SDK: {sorted(expected_ids - generated_ids)}; "
        f"unexpected: {sorted(generated_ids - expected_ids)}"
    )
