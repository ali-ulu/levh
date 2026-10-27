import { describe, expect, it } from "vitest";
import { THEME_STORAGE_KEY, themeBootstrapScript } from "./theme";

// The pre-paint theme script (#567) is inlined into <head> as a string, so a
// mistake in it is invisible to tsc and only shows up as an uncaught page error
// at runtime — exactly what the e2e "no client-side crash" spec asserts against.
// These tests run the string the browser would run.

function run(script: string) {
  new Function(script)();
}

describe("themeBootstrapScript", () => {
  it("is syntactically valid JavaScript", () => {
    expect(() => run(themeBootstrapScript())).not.toThrow();
  });

  it("does not leak a client-reference stub for the storage key", () => {
    // The regression: interpolating a value imported from a "use client" module
    // produced a throwing function body in place of the literal key.
    expect(themeBootstrapScript()).not.toContain("Attempted to call");
    expect(themeBootstrapScript()).toContain(THEME_STORAGE_KEY);
  });

  it("adds the dark class when the cookie says dark", () => {
    document.cookie = `${THEME_STORAGE_KEY}=dark; path=/`;
    document.documentElement.className = "";
    run(themeBootstrapScript());
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    document.cookie = `${THEME_STORAGE_KEY}=; path=/; max-age=0`;
  });

  it("removes the dark class when the cookie says light", () => {
    document.cookie = `${THEME_STORAGE_KEY}=light; path=/`;
    document.documentElement.className = "dark";
    run(themeBootstrapScript());
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    document.cookie = `${THEME_STORAGE_KEY}=; path=/; max-age=0`;
  });
});
