import { expect, test } from "@playwright/test";
import { NOAUTH_URL } from "./urls";

test("the locale switcher persists Turkish across reloads", async ({ page }) => {
  await page.goto(`${NOAUTH_URL}/`);

  const switcher = page.getByRole("button", { name: "Switch to Türkçe" });
  await expect(switcher).toBeVisible();
  await switcher.click();

  await expect(page.getByRole("link", { name: "Tüm bellekler" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "tr");
  expect(
    await page.evaluate(() => window.localStorage.getItem("levh_locale")),
  ).toBe("tr");

  await page.reload();

  await expect(page.getByRole("link", { name: "Tüm bellekler" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "İngilizce diline geç" }),
  ).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "tr");
});
