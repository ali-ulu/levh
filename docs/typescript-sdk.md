# TypeScript SDK

A typed client for the REST API, for Node 22.6+ and browsers. The package lives
in [`sdk/typescript/`](../sdk/typescript), and its own README covers install and
usage; this page explains how it stays honest about the API.

## It is generated, not hand-written

`sdk/typescript/src/generated/types.ts` and `endpoints.ts` are produced from the
repository's committed `openapi.json` by:

```bash
python scripts/generate_sdk.py          # write
python scripts/generate_sdk.py --check  # fail if stale
```

`openapi.json` is the frozen contract. `tests/test_openapi_contract.py` fails if
a code change alters the schema without regenerating the file in the same
commit, and `tests/test_typescript_sdk.py` fails if the committed SDK output
differs from what the contract generates. So a client built from this contract
cannot silently diverge from the API the server actually serves — the two drift
tests would catch it first.

Do not edit anything under `sdk/typescript/src/generated/` by hand. The next
regeneration reverts it, and the drift test fails in the meantime.

## Request types are exact; response types follow the contract

The contract declares request bodies — so `StoreRequest`, `RecallRequest` and
the other request schemas are generated as precise TypeScript types.

Response schemas now exist for an initial set of operations (health, stats,
memories list/store/get, fading, review queue, ask, consolidate, review
decision): those are generated as `Memory`, `HealthResponse`, `AskResponse`
and friends, and the client's convenience methods return them. The remaining
operations still return `unknown` — the response models are added route by
route, because each one needs its shape verified against what the handler
actually returns, not guessed. A hand-written `interface RecallResponse`
written ahead of the server would compile, satisfy callers, and then quietly
stop matching the server the first time a field was renamed.

`test_the_client_types_responses_as_unknown_until_the_contract_does_not` in
`tests/test_typescript_sdk.py` asserts the ratio honestly: every operation
that declares a 200 schema must be reachable as a generated type, and the
count of typed operations is asserted against the contract rather than
hard-coded, so the next route that grows a response model fails the test
until the client is regenerated.

## No dependencies, no build step

The package ships TypeScript source. Node 22.6+ strips type annotations
natively, so `node --test` runs the shipped `.ts` files directly and there is no
bundler, no transpile step, and no lockfile. The only runtime requirement is
`fetch`, which the caller can inject — useful for tests and for proxying.

## CI

The `sdk-typescript` job in `.github/workflows/ci.yml` runs `npm test` and
`tsc --noEmit`. The contract-drift half runs inside the Python suite, where the
generator can be imported and compared against the committed files.
