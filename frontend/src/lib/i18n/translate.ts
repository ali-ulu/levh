import en from "./en.json";

export const DEFAULT_LOCALE = "en";

export type TranslationVars = Record<string, string | number>;

type Catalogue = Record<string, string>;

const CATALOGUES: Record<string, Catalogue> = { en };

/** Resolves `key` in `locale`, falling back to `en` and then to the key itself. */
export function translate(
  key: string,
  vars?: TranslationVars,
  locale: string = DEFAULT_LOCALE,
): string {
  const catalogue = CATALOGUES[locale] ?? CATALOGUES[DEFAULT_LOCALE];
  const template = catalogue[key] ?? CATALOGUES[DEFAULT_LOCALE][key] ?? key;
  if (!vars) return template;

  return template.replace(/\{(\w+)\}/g, (match, name) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : match
  );
}
