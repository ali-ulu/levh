/**
 * A typed client over the LEVH REST API.
 *
 * The operation table in `generated/endpoints.ts` and the request/response
 * types in `generated/types.ts` are produced from the committed
 * `openapi.json`, which the server's own contract test keeps in sync with the
 * app. So `call()` can only be given an operation the API actually publishes,
 * and both halves of every signature come from the same source.
 *
 * Response typing is proportional to the contract: operations that declare a
 * 200 schema get that generated type through the operation map, and the rest
 * stay `unknown`. A hand-written interface would silently stop matching the
 * server — an honest `unknown` beats one of those.
 *
 * Works in Node 18+, browsers, and edge runtimes: the only dependency is
 * `fetch`, which the caller can inject.
 */

import { OPERATIONS, type OperationId } from "./generated/endpoints.ts";
import type {
  AskRequest,
  AskResponse,
  HealthResponse,
  Memory,
  MemoryStats,
  RecallRequest,
  StoreRequest,
} from "./generated/types.ts";

export interface LevhClientOptions {
  /** Origin of the LEVH server. Required outside the browser, where `fetch` has no origin to resolve against. */
  baseUrl?: string;
  /** Value for the `X-LEVH-Token` header. Omit for a loopback, tokenless server. */
  token?: string;
  /** Custom fetch. Defaults to the global one; inject it to test or to proxy. */
  fetch?: typeof globalThis.fetch;
}

export interface CallOptions {
  /** Values for `{placeholder}` segments in the operation's path. */
  path?: Record<string, string | number>;
  /** Query parameters. `undefined` and `null` are dropped. */
  query?: Record<string, string | number | boolean | undefined | null>;
  /** Request body, serialised as JSON. */
  body?: unknown;
  signal?: AbortSignal;
}

/**
 * Drop trailing slashes so joining with an operation path cannot produce `//`.
 *
 * Written as a scan rather than `.replace(/\/+$/, "")`: that regex is
 * polynomial on pathological input (CodeQL js/polynomial-redos), and the call
 * site is a caller-supplied `baseUrl`.
 */
function trimTrailingSlashes(value: string): string {
  let end = value.length;
  while (end > 0 && value[end - 1] === "/") {
    end -= 1;
  }
  return value.slice(0, end);
}

/** A non-2xx response. `detail` carries the server's error body when it sent one. */
export class LevhApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, detail: unknown) {
    super(`LEVH API error ${status}: ${describe(detail)}`);
    this.name = "LevhApiError";
    this.status = status;
    this.detail = detail;
  }
}

function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "detail" in detail) {
    return describe((detail as { detail: unknown }).detail);
  }
  try {
    return JSON.stringify(detail);
  } catch {
    return String(detail);
  }
}

export class LevhClient {
  readonly #baseUrl: string;
  readonly #token: string;
  readonly #fetch: typeof globalThis.fetch;

  constructor(options: LevhClientOptions = {}) {
    this.#baseUrl = trimTrailingSlashes(options.baseUrl ?? "");
    this.#token = options.token ?? "";
    this.#fetch = options.fetch ?? globalThis.fetch;

    if (typeof this.#fetch !== "function") {
      throw new TypeError(
        "No fetch available. Pass options.fetch, or run on Node 18+ / a browser.",
      );
    }
  }

  /** The URL `call()` would request, without sending it. Useful in tests and logs. */
  url(operation: OperationId, options: CallOptions = {}): string {
    const spec = OPERATIONS[operation];
    const path = this.#fillPath(operation, spec.path, spec.pathParams, options);
    const query = this.#queryString(spec.query, options.query);
    return `${this.#baseUrl}${path}${query}`;
  }

  /** Invoke a published operation. Throws `LevhApiError` on a non-2xx response.
   *
   * `T` stays `unknown` by default: the contract declares response schemas
   * for an initial set of operations only, and the generated `types.ts` is
   * the source for anything you can safely assert. Prefer the convenience
   * methods below, which carry the contract's own response types.
   */
  async call<T = unknown>(operation: OperationId, options: CallOptions = {}): Promise<T> {
    const spec = OPERATIONS[operation];
    const headers: Record<string, string> = { Accept: "application/json" };
    if (this.#token) headers["X-LEVH-Token"] = this.#token;

    const init: RequestInit = { method: spec.method, headers };
    if (options.body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    if (options.signal) init.signal = options.signal;

    const response = await this.#fetch(this.url(operation, options), init);

    if (!response.ok) {
      throw new LevhApiError(response.status, await readDetail(response));
    }
    if (response.status === 204) return undefined as T;

    const text = await response.text();
    if (!text) return undefined as T;
    try {
      return JSON.parse(text) as T;
    } catch {
      throw new LevhApiError(response.status, text);
    }
  }

  // ── the four operations almost every integration starts with ──────────

  health(): Promise<HealthResponse> {
    return this.call("health_api_health_get");
  }

  listMemories(
    query?: Record<string, string | number | boolean | undefined | null>,
  ): Promise<Memory[]> {
    return this.call("list_memories_api_memories_get_v1", { query });
  }

  storeMemory(body: StoreRequest): Promise<Memory> {
    return this.call("store_memory_api_memories_post_v1", { body });
  }

  recallMemories(body: RecallRequest): Promise<unknown> {
    return this.call("recall_memories_api_memories_recall_post_v1", { body });
  }

  ask(body: AskRequest): Promise<AskResponse> {
    return this.call("ask_memory_api_ask_post_v1", { body });
  }

  stats(): Promise<MemoryStats> {
    return this.call("get_stats_api_stats_get_v1");
  }

  #fillPath(
    operation: OperationId,
    path: string,
    pathParams: readonly string[],
    options: CallOptions,
  ): string {
    let filled = path;
    for (const name of pathParams) {
      const value = options.path?.[name];
      if (value === undefined) {
        throw new TypeError(`${operation} requires path parameter "${name}"`);
      }
      filled = filled.replace(`{${name}}`, encodeURIComponent(String(value)));
    }
    return filled;
  }

  #queryString(
    declared: readonly { name: string; required: boolean; type: string }[],
    values?: Record<string, string | number | boolean | undefined | null>,
  ): string {
    const params = new URLSearchParams();
    for (const param of declared) {
      const value = values?.[param.name];
      if (value === undefined || value === null) {
        // A required parameter that is missing is a caller bug, and letting it
        // through produces a 422 the caller then has to reverse-engineer.
        if (param.required) {
          throw new TypeError(`missing required query parameter "${param.name}"`);
        }
        continue;
      }
      params.set(param.name, String(value));
    }
    const encoded = params.toString();
    return encoded ? `?${encoded}` : "";
  }
}

async function readDetail(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}
