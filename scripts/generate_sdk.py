#!/usr/bin/env python3
"""Generate the TypeScript SDK's types from the committed OpenAPI contract.

The contract at the repo root is frozen by ``tests/test_openapi_contract.py``:
it is regenerated only in the commit that changes the API. So it, rather than
the FastAPI app, is the SDK's input — a client generated from the live app
would drift from the published contract the moment the two disagree, and the
contract is the thing clients are told to code against.

Output lands in ``sdk/typescript/src/generated/``. ``tests/test_typescript_sdk.py``
regenerates into a temp directory and fails if the committed output differs, so
a contract change that forgets the SDK is caught by the normal test run.

Usage:
    python scripts/generate_sdk.py            # write the files
    python scripts/generate_sdk.py --check    # fail if they are stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT = REPO_ROOT / "openapi.json"
OUT_DIR = REPO_ROOT / "sdk" / "typescript" / "src" / "generated"

HEADER = """// GENERATED FILE — do not edit by hand.
//
// Produced by `python scripts/generate_sdk.py` from the committed `openapi.json`
// at the repository root. `tests/test_typescript_sdk.py` fails if this file and
// the contract disagree, so regenerate rather than hand-patching.
"""


def _load() -> dict:
    with CONTRACT.open(encoding="utf-8") as handle:
        return json.load(handle)


def _ts_type(schema: dict | bool, schemas: dict) -> str:
    """Render one OpenAPI schema node as a TypeScript type.

    Deliberately small. The FastAPI-generated contract uses a narrow set of
    constructs — primitives, ``$ref``, arrays, ``anyOf`` for optionality, and
    ``additionalProperties`` for free-form dicts — and anything outside that set
    becomes ``unknown`` rather than a guess. A wrong type is worse than none: it
    compiles and then lies.
    """
    if schema is True:
        return "unknown"
    if schema is False or not isinstance(schema, dict):
        return "never"

    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]

    if "anyOf" in schema:
        parts: list[str] = []
        for option in schema["anyOf"]:
            rendered = _ts_type(option, schemas)
            if rendered not in parts:
                parts.append(rendered)
        return " | ".join(parts) if parts else "unknown"

    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"])

    node_type = schema.get("type")

    if node_type == "array":
        return f"{_ts_type(schema.get('items', {}), schemas)}[]"

    if isinstance(node_type, list):
        # e.g. type: ["string", "null"]
        options = [dict(schema, type=t) for t in node_type]
        return " | ".join(dict.fromkeys(_ts_type(o, schemas) for o in options))

    if node_type == "object" or "properties" in schema or "additionalProperties" in schema:
        properties = schema.get("properties")
        if not properties:
            return "Record<string, unknown>"
        required = set(schema.get("required", []) or [])
        lines = []
        for name, prop in properties.items():
            optional = "" if name in required else "?"
            key = name if name.isidentifier() else json.dumps(name)
            lines.append(f"  {key}{optional}: {_ts_type(prop, schemas)};")
        return "{\n" + "\n".join(lines) + "\n}"

    primitives = {
        "string": "string",
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
    }
    return primitives.get(node_type, "unknown")


def _generate_types(contract: dict) -> str:
    schemas = contract.get("components", {}).get("schemas", {})
    lines = [HEADER, ""]

    for name in sorted(schemas):
        lines.append(f"export type {name} = {_ts_type(schemas[name], schemas)};")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _query_params(operation: dict) -> list[dict]:
    params = []
    for param in operation.get("parameters", []) or []:
        if param.get("in") != "query":
            continue
        params.append(
            {
                "name": param["name"],
                "required": bool(param.get("required")),
                "type": _ts_type(param.get("schema", {}), {}),
            }
        )
    return params


def _path_params(operation: dict) -> list[str]:
    return [
        param["name"]
        for param in operation.get("parameters", []) or []
        if param.get("in") == "path"
    ]


def _body_type(operation: dict) -> str:
    content = (operation.get("requestBody") or {}).get("content") or {}
    schema = content.get("application/json", {}).get("schema")
    if schema is None:
        return "never"
    return _ts_type(schema, {})


def _generate_endpoints(contract: dict) -> str:
    lines = [HEADER, "", "export interface Operation {", "  method: string;"]
    lines += [
        "  path: string;",
        "  pathParams: readonly string[];",
        '  query: { name: string; required: boolean; type: string }[];',
        "  body: string;",
        "}",
        "",
        "export const OPERATIONS = {",
    ]

    # Sorted for a deterministic diff: the contract's key order is an
    # implementation detail of FastAPI, not a decision anyone made.
    for path in sorted(contract.get("paths", {})):
        operations = contract["paths"][path]
        for method in sorted(operations):
            operation = operations[method]
            op_id = operation.get("operationId")
            if not op_id:
                continue
            params = _query_params(operation)
            path_params = _path_params(operation)
            body = _body_type(operation)
            query = (
                "[" + ", ".join(
                    "{ name: %s, required: %s, type: %s }"
                    % (json.dumps(p["name"]), "true" if p["required"] else "false", json.dumps(p["type"]))
                    for p in params
                ) + "]"
            )
            lines.append(
                f"  {json.dumps(op_id)}: {{ method: {json.dumps(method.upper())}, "
                f"path: {json.dumps(path)}, "
                f"pathParams: [{', '.join(json.dumps(p) for p in path_params)}], "
                f"query: {query}, body: {json.dumps(body)} }},"
            )

    lines += ["} as const satisfies Record<string, Operation>;", ""]
    lines.append("export type OperationId = keyof typeof OPERATIONS;")
    lines.append("")

    return "\n".join(lines)


def generate() -> dict[str, str]:
    contract = _load()
    return {
        "types.ts": _generate_types(contract),
        "endpoints.ts": _generate_endpoints(contract),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail with a diff summary instead of writing",
    )
    args = parser.parse_args(argv)

    files = generate()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    stale: list[str] = []
    for name, content in files.items():
        target = OUT_DIR / name
        existing = target.read_text(encoding="utf-8") if target.exists() else None
        if existing == content:
            continue
        stale.append(name)
        if not args.check:
            target.write_text(content, encoding="utf-8")
            print(f"wrote {target.relative_to(REPO_ROOT)}")

    if args.check and stale:
        print(
            "the TypeScript SDK is stale; run `python scripts/generate_sdk.py`: "
            + ", ".join(stale),
            file=sys.stderr,
        )
        return 1

    if not stale:
        print("the TypeScript SDK is up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
