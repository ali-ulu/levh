import { beforeEach, describe, expect, it } from "vitest";
import {
  ONBOARDING_STORAGE_KEY,
  dismissOnboarding,
  isOnboardingDismissed,
  resetOnboarding,
} from "./onboarding";

// The onboarding flag (#565) is what stops the getting-started card returning
// on every server restart. The round trip is the whole contract.

describe("onboarding flag", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("is not dismissed before anything is recorded", () => {
    expect(isOnboardingDismissed()).toBe(false);
  });

  it("stays dismissed once recorded, across a fresh read", () => {
    dismissOnboarding();
    expect(window.localStorage.getItem(ONBOARDING_STORAGE_KEY)).toBe("true");
    expect(isOnboardingDismissed()).toBe(true);
  });

  it("returns to pending after a reset", () => {
    dismissOnboarding();
    resetOnboarding();
    expect(isOnboardingDismissed()).toBe(false);
  });

  it("treats an unrelated stored value as not dismissed", () => {
    window.localStorage.setItem(ONBOARDING_STORAGE_KEY, "yes");
    expect(isOnboardingDismissed()).toBe(false);
  });
});
