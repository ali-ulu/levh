import { beforeEach, describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  LOCALE_STORAGE_KEY,
  LocaleProvider,
  useLocale,
  useT,
} from ".";

function Probe() {
  const { locale, setLocale } = useLocale();
  const t = useT();
  return (
    <div>
      <span data-testid="locale">{locale}</span>
      <span>{t("app.settings.title")}</span>
      <button
        type="button"
        onClick={() => setLocale(locale === "en" ? "tr" : "en")}
      >
        toggle
      </button>
    </div>
  );
}

describe("LocaleProvider", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.lang = "en";
  });

  it("loads the persisted Turkish locale and updates the document language", async () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "tr");

    render(
      <LocaleProvider>
        <Probe />
      </LocaleProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("locale")).toHaveTextContent("tr");
    });
    expect(screen.getByText("Ayarlar")).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("tr");
  });

  it("persists an explicit locale switch", async () => {
    const user = userEvent.setup();

    render(
      <LocaleProvider>
        <Probe />
      </LocaleProvider>,
    );

    await user.click(screen.getByRole("button", { name: "toggle" }));

    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("tr");
    expect(screen.getByText("Ayarlar")).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("tr");
  });

  it("falls back to English for an unsupported stored locale", async () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "de");

    render(
      <LocaleProvider>
        <Probe />
      </LocaleProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("locale")).toHaveTextContent("en");
    });
    expect(screen.getByText("Settings")).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("en");
  });
});
