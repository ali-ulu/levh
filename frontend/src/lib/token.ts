// Client-side storage for the optional LEVH server access token.
//
// The FastAPI backend can be started with LEVH_TOKEN set, which gates
// every /api/* request (except /api/health) behind an X-LEVH-Token
// header, and the /ws/memory socket behind that header or a ?token= param.
// This module is the single source of truth for that token in the browser.
//
// SSR-safe: this app uses static export, so module code and effects can run
// in a non-DOM context. Every window/localStorage access is guarded.

const STORAGE_KEY = "levh_token";
const LEGACY_STORAGE_KEY = "stackmemory_token";
const CHANGE_EVENT = "levh-token-changed";

/** Returns the stored token, or "" when none is set / not in a browser. */
export function getToken(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(STORAGE_KEY)
      ?? window.localStorage.getItem(LEGACY_STORAGE_KEY)
      ?? "";
  } catch {
    return "";
  }
}

/**
 * Stores a token (trimmed). An empty/whitespace value removes it.
 * Notifies listeners via a window CustomEvent so the auth gate can re-check.
 */
export function setToken(value: string): void {
  if (typeof window === "undefined") return;
  const trimmed = value.trim();
  try {
    if (trimmed) {
      window.localStorage.setItem(STORAGE_KEY, trimmed);
    } else {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // Ignore storage failures (private mode, quota) — the app still runs.
  }
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
}

/** Removes the stored token and notifies listeners. */
export function clearToken(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Ignore storage failures.
  }
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
}

/**
 * Subscribes to token changes. Returns an unsubscribe function.
 * No-op (returns a no-op unsubscribe) outside the browser.
 */
export function onTokenChange(cb: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(CHANGE_EVENT, cb);
  return () => window.removeEventListener(CHANGE_EVENT, cb);
}
