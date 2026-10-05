import { describe, expect, it } from "vitest";
import en from "./i18n/en.json";
import { translate as publicTranslate } from "./i18n";
import { DEFAULT_LOCALE, translate } from "./i18n/translate";

describe("translate", () => {
  it("stays available through the client-facing public module", () => {
    expect(publicTranslate("app.settings.title")).toBe("Settings");
  });

  it("resolves build-time metadata through the server-safe module", () => {
    expect(translate("app.metadata.title")).toBe("LEVH");
    expect(translate("app.metadata.description")).toContain("local-first");
    expect(translate("app.metadata.applicationName")).toBe("LEVH");
  });

  it("resolves a key from the en catalogue", () => {
    expect(translate("app.settings.title")).toBe("Settings");
    expect(translate("ui.dialog.close")).toBe("Close");
  });

  it("interpolates {name} placeholders", () => {
    // The catalogue is static, so the interpolation path is exercised through a
    // key that carries no placeholder today; a missing var is left verbatim
    // rather than replaced with "undefined", which is the failure a reader sees.
    expect(translate("ui.dialog.close", { ignored: "x" })).toBe("Close");
    expect(translate("missing.{name}", { name: "value" })).toBe("missing.value");
    expect(translate("missing.{name}")).toBe("missing.{name}");
    // The app chrome uses the same path for its composed labels.
    expect(translate("theme.switcher.switchTo", { theme: "Deep Space" })).toBe(
      "Switch to Deep Space theme"
    );
  });

  it("resolves the app chrome keys the layout converts to", () => {
    expect(translate("sidebar.nav.overview")).toBe("Overview");
    expect(translate("header.action.quickCapture")).toBe("Quick capture");
    expect(translate("header.help.page.overview.desc")).toContain("Dashboard");
  });

  it("resolves the page keys the follow-on conversions use", () => {
    expect(translate("app.error.title")).toBe("Something went wrong");
    expect(translate("auth.gate.unlock")).toBe("Unlock");
    expect(translate("memoryResultCard.viewDetails")).toBe("View details");
    // The timeline count is composed, so the plural forms are checked with a
    // real count rather than by the key alone.
    expect(translate("app.timeline.count.one", { count: 1 })).toBe("1 memory");
    expect(translate("app.timeline.count.other", { count: 3 })).toBe("3 memories");
  });

  it("resolves the dashboard widget keys the latest batch converts to", () => {
    expect(translate("fadingMemories.title")).toBe("Fading Memories");
    expect(translate("fadingMemories.needReview", { count: 2 })).toBe("2 need review");
    expect(translate("liveFeed.event.sessionCreated")).toBe("session started");
    expect(translate("liveFeed.detail.recalled", { query: "sqlite", count: 1 })).toBe(
      '"sqlite" → 1 results'
    );
    expect(translate("liveFeed.detail.summarized", { count: 5 })).toBe(
      "session distilled from 5 memories"
    );
    expect(translate("knowledgeConstellation.records", { count: "1,204" })).toBe("1,204 records");
  });

  it("falls back to the key when it is not in the catalogue", () => {
    // A silent empty string hides a typo'd key; returning the key makes the
    // mistake visible in the rendered page and in a snapshot.
    expect(translate("does.not.exist")).toBe("does.not.exist");
  });

  it("falls back to en for an unknown locale", () => {
    expect(translate("app.settings.title", undefined, "zz")).toBe("Settings");
  });

  it("defaults to en", () => {
    expect(DEFAULT_LOCALE).toBe("en");
  });
});

describe("the en catalogue", () => {
  it("has no empty values", () => {
    const empty = Object.entries(en).filter(([, value]) => value.trim() === "");
    expect(empty).toEqual([]);
  });
});
