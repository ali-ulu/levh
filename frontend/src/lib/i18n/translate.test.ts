import { describe, expect, it } from "vitest";
import en from "./en.json";
import tr from "./tr.json";
import { translate } from "./translate";

function placeholders(value: string): string[] {
  return (value.match(/\{\w+\}/g) ?? [])
    .map((token) => token.slice(1, -1))
    .sort();
}

describe("i18n catalogues", () => {
  it("keeps Turkish keys and interpolation variables in parity with English", () => {
    expect(Object.keys(tr).sort()).toEqual(Object.keys(en).sort());

    for (const key of Object.keys(en)) {
      expect(placeholders(tr[key as keyof typeof tr])).toEqual(
        placeholders(en[key as keyof typeof en]),
      );
    }
  });

  it("translates Turkish copy and interpolates variables", () => {
    expect(translate("app.settings.title", undefined, "tr")).toBe("Ayarlar");
    expect(
      translate("locale.switcher.switchTo", { language: "Türkçe" }, "en"),
    ).toBe("Switch to Türkçe");
  });

  it("falls back to English for unsupported locales and unknown keys", () => {
    expect(translate("app.settings.title", undefined, "de")).toBe("Settings");
    expect(translate("missing.translation.key", undefined, "tr")).toBe(
      "missing.translation.key",
    );
  });
});
