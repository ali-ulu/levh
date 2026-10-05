import en from "./en.json";
import tr from "./tr.json";

export const DEFAULT_LOCALE = "en" as const;
export const SUPPORTED_LOCALES = ["en", "tr"] as const;

export type Locale = (typeof SUPPORTED_LOCALES)[number];
export type TranslationVars = Record<string, string | number>;

type Catalogue = Record<string, string>;

const CATALOGUES: Record<Locale, Catalogue> = { en, tr };

export function normalizeLocale(locale: string | null | undefined): Locale {
  return SUPPORTED_LOCALES.includes(locale as Locale)
    ? (locale as Locale)
    : DEFAULT_LOCALE;
}

/** Resolves `key` in `locale`, falling back to `en` and then to the key itself. */
export function translate(
  key: string,
  vars?: TranslationVars,
  locale: string = DEFAULT_LOCALE,
): string {
  const normalizedLocale = normalizeLocale(locale);
  const catalogue = CATALOGUES[normalizedLocale];
  const template = catalogue[key] ?? CATALOGUES[DEFAULT_LOCALE][key] ?? key;
  if (!vars) return template;

  return template.replace(/\{(\w+)\}/g, (match, name) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : match
  );
}
