// `@types/jest-axe` augments the Jest `Matchers` interface; vitest's `expect`
// is a different object, so the matcher registered in `vitest.setup.ts` has no
// type until it is declared here. Without this, `next build` fails on
// `expect(await axe(...)).toHaveNoViolations()` even though the test passes.
import "vitest";

declare module "vitest" {
  interface Assertion<T = unknown> {
    toHaveNoViolations(): T;
  }
  interface AsymmetricMatchersContaining {
    toHaveNoViolations(): void;
  }
}
