// Where the E2E suite points. Kept out of playwright.config.ts so specs can
// import them without pulling in the config (and its webServer side effects).

export const NOAUTH_URL = "http://127.0.0.1:8930";
export const AUTH_URL = "http://127.0.0.1:8931";
export const AUTH_TOKEN = "e2e-shared-secret";

// The token localStorage key the app reads (see src/lib/token.ts).
export const TOKEN_STORAGE_KEY = "levh_token";
