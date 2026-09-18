import { describe, expect, it, vi } from "vitest";
import { clearToken, getToken, onTokenChange, setToken } from "./token";

const STORAGE_KEY = "levh_token";
const LEGACY_KEY = "stackmemory_token";

describe("token storage", () => {
  it("returns an empty string when no token is stored", () => {
    expect(getToken()).toBe("");
  });

  it("stores a trimmed token under the canonical key", () => {
    setToken("  secret-token  ");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("secret-token");
    expect(getToken()).toBe("secret-token");
  });

  it("falls back to the legacy key when the canonical one is absent", () => {
    window.localStorage.setItem(LEGACY_KEY, "legacy-token");
    expect(getToken()).toBe("legacy-token");
  });

  it("prefers the canonical key over the legacy one", () => {
    window.localStorage.setItem(STORAGE_KEY, "current");
    window.localStorage.setItem(LEGACY_KEY, "legacy");
    expect(getToken()).toBe("current");
  });

  it("removes the token when set with an empty value", () => {
    setToken("secret");
    setToken("   ");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(getToken()).toBe("");
  });

  it("clears the stored token", () => {
    setToken("secret");
    clearToken();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(getToken()).toBe("");
  });

  it("emits a change event on set and clear", () => {
    const listener = vi.fn();
    const unsubscribe = onTokenChange(listener);
    setToken("secret");
    clearToken();
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
    setToken("again");
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("degrades to an empty token when storage access throws", () => {
    const getItem = vi
      .spyOn(window.localStorage, "getItem")
      .mockImplementation(() => {
        throw new Error("storage disabled");
      });
    expect(getToken()).toBe("");
    getItem.mockRestore();
  });

  it("does not throw when storage writes fail", () => {
    const setItem = vi
      .spyOn(window.localStorage, "setItem")
      .mockImplementation(() => {
        throw new Error("quota exceeded");
      });
    expect(() => setToken("secret")).not.toThrow();
    setItem.mockRestore();
  });
});