"use client";

// Frontend i18n (#308). The extraction mechanism is a flat, colocated-*key*
// catalogue: components call `useT()` and address strings by a stable dotted
// key (`settings.accessToken.title`), and every key resolves against
// `src/lib/i18n/en.json`. This is the Next.js-built-in route — no runtime
// library, no ICU message format — chosen because the dashboard ships as a
// static export and must stay offline and dependency-light.
//
// Design decisions this module fixes (see docs/internal/I18N-DESIGN.md):
//
// 1. Mechanism: hand-managed dictionary + a `t()` hook, not next-intl/lingui.
//    The message set here needs interpolation only; ICU plurals are out of
//    scope, and a runtime would be a new shipped dependency on a static export.
// 2. Where strings live: one catalogue per locale (`en.json`), addressed by
//    key. The key is colocated with the component in spirit (its prefix names
//    the surface) while the copy stays in one file a translator can be handed.
// 3. Locale detection/persistence: an explicit choice stored in
//    `localStorage`, mirroring the token persistence in `src/lib/token.ts`. The
//    default is `en`. Detection is deliberately *not* `Accept-Language` yet —
//    the static export has no request-time negotiation, so a header-based
//    locale would need a second mechanism later; one switcher is enough until a
//    second locale exists.
// 4. Static export: the locale is a client value, **not** a route segment, so
//    `output: "export"` is unchanged and the export does not multiply per
//    locale. A locale route segment would have to be decided before the first
//    page was converted; this mechanism avoids that constraint entirely.
// 5. Translations: only `en` ships. The mechanism exists so a second locale is
//    a data change (a new JSON file) rather than a refactor. No locale is
//    promised or maintained until one is added deliberately.
//
// The drift gate in `frontend/scripts/check-ui-strings.mjs` is what makes the
// conversion stick: a user-visible literal that appears outside the catalogue
// fails CI. See `docs/internal/I18N-DESIGN.md`.
import { useCallback } from "react";
import { translate, type TranslationVars } from "./translate";

export { DEFAULT_LOCALE, translate } from "./translate";

/**
 * Component-facing translator. Returns `t(key, vars?)`.
 *
 * No locale switcher is wired yet: with one locale the choice has nothing to
 * switch to, and the persistence shape (localStorage, key `levh_locale`) is
 * recorded here so the switcher lands without touching call sites.
 */
export function useT(): (key: string, vars?: TranslationVars) => string {
  return useCallback((key: string, vars?: TranslationVars) => translate(key, vars), []);
}
