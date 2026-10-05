# Frontend i18n: extraction mechanism (#308)

Tarih: 2026-10-02 · Durum: karar verildi · Tür: tasarım kararı

Design issue #308 asked for the extraction mechanism to be settled **before**
any page was converted, because the mechanism decides how much of the ~20 pages
has to be touched twice. This file records the decisions and the proof that the
mechanism works. It is internal: it is a maintainer decision record, not a
user-facing promise that more than one language ships.

## Decisions

### 1. Extraction mechanism — hand-managed catalogue, not a library

A flat, key-addressed JSON catalogue (`frontend/src/lib/i18n/en.json`) read by
a pure server-safe translator (`frontend/src/lib/i18n/translate.ts`) plus the
client hook module (`frontend/src/lib/i18n/index.ts`). The client module keeps
the public `translate()` export for compatibility and exposes `useT()`.

Rejected: `next-intl` / `react-i18next` / `@lingui`. Each brings a runtime, a
message format (ICU pluralisation, interpolation) and a build integration. The
dashboard is a **static export** (`output: "export"`) shipped inside the Python
wheel and must stay offline and dependency-light. The message set this UI needs
is interpolation only; plurals are not a requirement until a locale needs them,
and adding them later is a change to `translate()`, not to call sites.

Trade-off accepted: no ICU plurals, no compile-time extraction, no
translator-facing tooling. The drift gate below is what compensates for the last
one.

### 2. Where strings live — one catalogue per locale, addressed by key

Keys are dotted and prefixed by surface (`app.settings.title`,
`settings.accessToken.save`, `ui.dialog.close`). The prefix keeps the key
colocated with the component *in spirit* while the copy stays in one file a
translator can be handed. A missing key resolves to the key itself, never to an
empty string, so a typo is visible in the rendered page.

### 3. Locale detection and persistence — explicit, `localStorage`, default `en`

The chosen locale is stored in `localStorage` (key `levh_locale`), mirroring the
token persistence in `src/lib/token.ts`. No switcher is wired yet: with one
locale there is nothing to switch to, and wiring a control that does nothing is
worse than the documented shape. Detection is deliberately **not**
`Accept-Language`: the static export has no request-time negotiation, so a
header-based locale would need a second mechanism later.

### 4. Static export — locale is a client value, not a route segment

`output: "export"` is unchanged. The export does not multiply per locale. This
is the decision that had to come first: a locale route segment (`/[locale]/…`)
would multiply every exported page and force the choice before the first
conversion. The client-value approach avoids that constraint entirely.

### 5. No locale beyond `en`

Only `en` ships. The mechanism exists so a second locale is a data change (a new
JSON file plus a switcher) rather than a refactor. No locale is promised or
maintained until one is added deliberately.

## Proof conversion

Three files were converted to land the mechanism end to end:

- `src/app/settings/page.tsx` — the page heading and subtitle.
- `src/app/settings/_sections/access-token.tsx` — labels, placeholder, buttons,
  the status badge and the help paragraph.
- `src/components/ui/dialog.tsx` — the shared close button's `aria-label` and
  its screen-reader text. This is the case the issue named explicitly: a
  translation pass that misses an `aria-label` ships a half-translated page to a
  screen reader.

The first follow-on conversion is the **app chrome**, which every page renders:

- `src/components/layout/theme-switcher.tsx` — fully converted (6 → 0); its
  labels are composed with `{theme}` interpolation.
- `src/components/layout/sidebar.tsx` — 27 → 2. The nav model now carries
  `labelKey` instead of English copy; the 2 remaining hits are the brand name
  `LEVH` and the version badge, which `scripts/release.py` matches by regex and
  must stay literal.
- `src/components/layout/header.tsx` — 30 → 3. The 3 remaining hits are the DOM
  tag names in the keyboard-shortcut guard (`INPUT`/`TEXTAREA`/`SELECT`), which
  are compared against `event.target.tagName`, not rendered.

`src/app/layout.tsx` is now converted through the server-safe
`src/lib/i18n/translate.ts` module. Next.js build-time metadata resolves the
title, description, and application name from the same catalogue without
importing the client-only `useT()` hook. Its ratchet falls from 4 to 1; the
remaining hit is the technical `afterInteractive` Script strategy token.

Nothing else was converted. The remaining files keep their literals and their
ratchet entries.

## The drift gate (the part that makes it stick)

`frontend/scripts/check-ui-strings.mjs` parses every non-test `.tsx` file under
`src/` with the TypeScript compiler API and counts user-visible string literals:
JSX text nodes and the `aria-label` / `aria-description` / `placeholder` /
`title` / `alt` attributes. It deliberately ignores module specifiers, Tailwind
class lists, `cn(...)` arguments, translation-call arguments (`t("…")`),
`displayName` assignments and `aria-hidden="true"` decorative subtrees. A
literal that is **exactly** a key in `en.json` is also skipped: the sidebar
holds its keys in a data model rather than inline in `t(...)`, and counting
those would make a fully-converted file look unconverted. That exception is not
heuristic — the string has to exist in the catalogue to qualify — so it cannot
swallow arbitrary copy. It deliberately does **not** reach the user-facing
attributes: `aria-label="header.action.help"` renders the key to a screen
reader, so it is still counted.

Because the tree is not converted yet, the check is a **ratchet**, the same
shape as the mypy `files` list in `pyproject.toml`: per-file counts live in
`frontend/scripts/ui-string-baseline.json` and may only go down. Adding a
literal to an already-counted file fails CI; converting a file means lowering
(or removing) its entry in the same PR, and a stale entry (a file that shrank
without the baseline being tightened) also fails. A ratchet that can grow
silently is not a gate.

`--update` regenerates the baseline; it is the deliberate act a conversion makes,
and it is reviewed as a one-file diff.

The gate runs in `npm test` (via `src/lib/ui-string-ratchet.test.ts`), which the
`frontend` CI job already runs, and directly as
`node scripts/check-ui-strings.mjs`.

## Sequencing (from the issue)

1. Settle the mechanism and where strings live — done here.
2. Convert one page and the primitives it uses — done here.
3. Add a test that fails on a literal outside the catalogue — done here.
4. Convert the remaining pages — **not done**; each page is its own change and
   lowers its own ratchet entry.

## Not in scope

- Adding a second locale's actual translations.
- Server error message translation (API `detail` strings are produced by the
  server and are a separate decision).
- Right-to-left layout support.
