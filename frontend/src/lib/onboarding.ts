// Onboarding completion flag (#565). The dashboard re-showed the onboarding
// card after every `levh serve` restart because completion was only ever
// tracked in server-side readiness, never remembered in the browser. This is
// the local memory of "the user has already been walked through this".
//
// localStorage is wrapped because it throws in some private/restricted
// contexts; a failure to read or write must degrade to "not dismissed" rather
// than break the page.
export const ONBOARDING_STORAGE_KEY = "levh-onboarded";

export function isOnboardingDismissed(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(ONBOARDING_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

export function dismissOnboarding(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(ONBOARDING_STORAGE_KEY, "true");
  } catch {
    // Storage unavailable — the card simply shows again next time.
  }
}

export function resetOnboarding(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(ONBOARDING_STORAGE_KEY);
  } catch {
    // Nothing to clear.
  }
}
