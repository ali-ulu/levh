/**
 * SDK tests. No dependencies and no network: a fake `fetch` records the request
 * the client would have sent and returns a scripted response.
 *
 * Run with `npm test` (node --test). Node strips the type annotations itself,
 * so the same `.ts` files that ship are the ones under test.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { LevhApiError, LevhClient } from "./client.ts";
import { OPERATIONS } from "./generated/endpoints.ts";

function recordingFetch(response: Response | (() => Response)) {
  const calls: { url: string; init: RequestInit }[] = [];
  const fn = (async (url: string | URL, init?: RequestInit) => {
    calls.push({ url: String(url), init: init ?? {} });
    return typeof response === "function" ? response() : response;
  }) as unknown as typeof globalThis.fetch;
  return { calls, fetch: fn };
}

const json = (body: unknown, status = 200) =>
  new Response(body === undefined ? "" : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

// ── URL construction ─────────────────────────────────────────────────

test("builds the URL from the operation table", () => {
  const client = new LevhClient({ baseUrl: "http://localhost:8000/" });
  assert.equal(client.url("health_api_health_get"), "http://localhost:8000/api/health");
  // The trailing slash in baseUrl must not produce a double slash.
  assert.equal(
    client.url("list_memories_api_memories_get_v1"),
    "http://localhost:8000/api/v1/memories",
  );
});

test("strips any number of trailing slashes, and only trailing ones", () => {
  const trailing = new LevhClient({ baseUrl: "http://localhost:8000/////" });
  assert.equal(trailing.url("health_api_health_get"), "http://localhost:8000/api/health");
  // A slash inside the path is not a trailing slash and must survive.
  const inner = new LevhClient({ baseUrl: "http://localhost:8000/levh" });
  assert.equal(inner.url("health_api_health_get"), "http://localhost:8000/levh/api/health");
  const none = new LevhClient({ baseUrl: "http://localhost:8000" });
  assert.equal(none.url("health_api_health_get"), "http://localhost:8000/api/health");
});

test("drops undefined query values and keeps false/0", () => {
  const client = new LevhClient({ baseUrl: "http://x" });
  const url = client.url("list_memories_api_memories_get_v1", {
    query: { limit: 10, offset: undefined, pinned: false, q: "" },
  });
  // `pinned: false` and `q: ""` are real values and must survive; a dropped
  // `pinned=false` would silently become an unfiltered listing.
  assert.match(url, /limit=10/);
  assert.match(url, /pinned=false/);
  assert.match(url, /q=/);
  assert.doesNotMatch(url, /offset/);
});

test("substitutes path parameters", () => {
  const client = new LevhClient({ baseUrl: "http://x" });
  const url = client.url("agent_collaboration_api_agents_collaboration__project__get_v1", {
    path: { project: "team one" },
  });
  assert.equal(url, "http://x/api/v1/agents/collaboration/team%20one");
});

test("a missing path parameter is a TypeError, not a request", () => {
  const client = new LevhClient({ baseUrl: "http://x" });
  assert.throws(
    () => client.url("agent_collaboration_api_agents_collaboration__project__get_v1"),
    TypeError,
  );
});

// ── request shape ────────────────────────────────────────────────────

test("sends the token header only when a token is configured", async () => {
  const withToken = recordingFetch(json({ ok: true }));
  await new LevhClient({ baseUrl: "http://x", token: "t", fetch: withToken.fetch }).health();
  assert.equal(
    (withToken.calls[0].init.headers as Record<string, string>)["X-LEVH-Token"],
    "t",
  );

  const without = recordingFetch(json({ ok: true }));
  await new LevhClient({ baseUrl: "http://x", fetch: without.fetch }).health();
  assert.equal(
    (without.calls[0].init.headers as Record<string, string>)["X-LEVH-Token"],
    undefined,
  );
});

test("serialises the body as JSON and sets Content-Type", async () => {
  const recorder = recordingFetch(json({ id: "m1" }));
  const client = new LevhClient({ baseUrl: "http://x", fetch: recorder.fetch });

  await client.storeMemory({ content: "remember this" });

  const { init } = recorder.calls[0];
  assert.equal(init.method, "POST");
  assert.equal((init.headers as Record<string, string>)["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(init.body as string), { content: "remember this" });
});

test("a GET sends no body and no Content-Type", async () => {
  const recorder = recordingFetch(json([]));
  await new LevhClient({ baseUrl: "http://x", fetch: recorder.fetch }).listMemories();

  const { init } = recorder.calls[0];
  assert.equal(init.method, "GET");
  assert.equal(init.body, undefined);
  assert.equal((init.headers as Record<string, string>)["Content-Type"], undefined);
});

// ── responses ────────────────────────────────────────────────────────

test("parses a JSON response", async () => {
  const recorder = recordingFetch(json({ memories: [{ id: "m1" }] }));
  const result = await new LevhClient({ baseUrl: "http://x", fetch: recorder.fetch }).listMemories();
  assert.deepEqual(result, { memories: [{ id: "m1" }] });
});

test("a 204 resolves to undefined rather than throwing on empty input", async () => {
  const recorder = recordingFetch(new Response(null, { status: 204 }));
  const result = await new LevhClient({ baseUrl: "http://x", fetch: recorder.fetch }).health();
  assert.equal(result, undefined);
});

test("a non-2xx raises LevhApiError carrying the status and detail", async () => {
  const recorder = recordingFetch(json({ detail: "memory not found" }, 404));
  const client = new LevhClient({ baseUrl: "http://x", fetch: recorder.fetch });

  await assert.rejects(
    () => client.listMemories({ q: "missing" }),
    (error: unknown) => {
      assert.ok(error instanceof LevhApiError);
      assert.equal(error.status, 404);
      assert.deepEqual(error.detail, { detail: "memory not found" });
      assert.match(error.message, /404/);
      assert.match(error.message, /memory not found/);
      return true;
    },
  );
});

// ── contract alignment ───────────────────────────────────────────────

test("the convenience methods name operations that exist in the contract", () => {
  // Guards against a rename in openapi.json leaving a method pointing at an
  // operation that no longer exists — which would fail only at call time.
  const client = new LevhClient({ baseUrl: "http://x" });
  for (const op of [
    "health_api_health_get",
    "list_memories_api_memories_get_v1",
    "store_memory_api_memories_post_v1",
    "recall_memories_api_memories_recall_post_v1",
    "ask_memory_api_ask_post_v1",
  ] as const) {
    assert.ok(OPERATIONS[op], `${op} is missing from the generated operation table`);
    assert.ok(client.url(op).startsWith("http://x/api/"));
  }
});
