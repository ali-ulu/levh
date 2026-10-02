import { describe, expect, it } from "vitest";
import en from "./i18n/en.json";
import { DEFAULT_LOCALE, translate } from "./i18n";

describe("translate", () => {
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
