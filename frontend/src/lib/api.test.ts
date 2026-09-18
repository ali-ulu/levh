import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, wsUrl } from "./api";
import { setToken } from "./token";

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

function lastCall(mock: ReturnType<typeof vi.fn>): [string, RequestInit] {
  return mock.mock.calls[mock.mock.calls.length - 1] as [string, RequestInit];
}

describe("api client", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("attaches the stored token as X-LEVH-Token", async () => {
    setToken("secret-token");
    fetchMock.mockResolvedValue(jsonResponse({ status: "ok" }));
    await api.health();
    const [, init] = lastCall(fetchMock);
    expect((init.headers as Record<string, string>)["X-LEVH-Token"]).toBe("secret-token");
  });

  it("omits the token header when none is stored", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ status: "ok" }));
    await api.health();
    const [, init] = lastCall(fetchMock);
    expect(init.headers).not.toHaveProperty("X-LEVH-Token");
  });

  it("maps a string detail into the error message", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: "not found" }, { status: 404 })
    );
    await expect(api.getMemory("abc")).rejects.toThrow("API error 404: not found");
  });

  it("joins a validation-error array into the error message", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { detail: [{ msg: "field required" }, { msg: "bad type" }] },
        { status: 422 }
      )
    );
    await expect(api.health()).rejects.toThrow(
      "API error 422: field required; bad type"
    );
  });

  it("unpacks an admission-gate object with reasons", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          detail: {
            message: "admission denied",
            decision: { reasons: ["low trust", "duplicate"] },
          },
        },
        { status: 403 }
      )
    );
    await expect(api.health()).rejects.toThrow(
      "API error 403: admission denied — low trust; duplicate"
    );
  });

  it("reports an invalid (non-JSON) success response", async () => {
    fetchMock.mockResolvedValue(
      new Response("<html>not json</html>", {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    await expect(api.health()).rejects.toThrow(
      "API error: invalid response from /api/health"
    );
  });

  it("drops empty query params instead of sending them", async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));
    await api.listMemories({ tag: "work", q: "", limit: 5 });
    const [url] = lastCall(fetchMock);
    expect(url).toBe("/api/memories?tag=work&limit=5");
  });

  it("builds the WebSocket URL with the token as a query param", () => {
    setToken("secret-token");
    const url = wsUrl();
    expect(url).toContain("/ws/memory?token=secret-token");
    expect(url.startsWith("ws://") || url.startsWith("wss://")).toBe(true);
  });

  it("omits the token query param when no token is stored", () => {
    expect(wsUrl()).toContain("/ws/memory");
    expect(wsUrl()).not.toContain("token=");
  });
});