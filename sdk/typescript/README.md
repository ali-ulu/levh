# LEVH TypeScript SDK

A typed client for the LEVH REST API, for Node 22.6+ and browsers.

LEVH is a local-first memory layer for AI agents: one SQLite file, one process,
and an API for storing, recalling and asking questions over memories.

## Install

```bash
npm install levh
```

## Use

```ts
import { LevhClient } from "levh";

const client = new LevhClient({
  baseUrl: "http://localhost:8000", // omit in the browser, where fetch resolves against the origin
  token: process.env.LEVH_TOKEN,    // omit for a loopback, tokenless server
});

await client.storeMemory({ content: "Deploy runs make deploy, then verify staging" });

const recalled = await client.recallMemories({ query: "how do we deploy" });
const answer = await client.ask({ question: "what is the deploy process?" });
```

Every published operation is reachable through `call`, not just the handful with
convenience methods:

```ts
const conflicts = await client.call("list_conflicts_api_conflicts_get_v1", {
  query: { status: "open", limit: 20 },
});
```

Non-2xx responses throw `LevhApiError` with `status` and `detail`:

```ts
import { LevhApiError } from "levh";

try {
  await client.storeMemory({ content: "x" });
} catch (error) {
  if (error instanceof LevhApiError && error.status === 409) {
    // the admission gate held this as a near-duplicate
  }
}
```

## Types

`src/generated/` is produced from the repository's committed `openapi.json` by
`python scripts/generate_sdk.py`. Do not edit it by hand:
`tests/test_typescript_sdk.py` regenerates into a temp directory and fails if the
committed output differs, so a hand-edit is reverted by the next regeneration and
fails the build in the meantime.

Request types are exact, because the contract declares request bodies. Response
types are `unknown`, because the contract declares **no** response schemas —
every 200 is an empty object. Returning a hand-written interface would compile
and then silently stop matching the server. When the API publishes response
models, regenerate and tighten `call`'s return type; the test that currently
asserts the absence of response schemas will tell you when that day arrives.

## Development

```bash
npm test          # node --test, no dependencies, no network
npm run typecheck # tsc --noEmit
npm run generate  # regenerate src/generated from openapi.json
```

Requires Node 22.6+, which strips TypeScript types natively — there is no build
step and no bundler in this package.
