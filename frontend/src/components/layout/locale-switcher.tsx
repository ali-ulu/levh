"use client";

import { Languages } from "lucide-react";
import { SUPPORTED_LOCALES, useLocale, useT } from "@/lib/i18n";

export function LocaleSwitcher() {
  const { locale, setLocale } = useLocale();
  const t = useT();
  const english = SUPPORTED_LOCALES[0];
  const turkish = SUPPORTED_LOCALES[1];
  const nextLocale = locale === english ? turkish : english;
  const currentLabel =
    locale === english
      ? t("locale.switcher.enShort")
      : t("locale.switcher.trShort");
  const nextLanguage =
    nextLocale === english
      ? t("locale.switcher.english")
      : t("locale.switcher.turkish");

  return (
    <button
      type="button"
      onClick={() => setLocale(nextLocale)}
      aria-label={t("locale.switcher.switchTo", { language: nextLanguage })}
      title={t("locale.switcher.title", { language: nextLanguage })}
      className="icon-button hidden min-w-12 items-center justify-center gap-1.5 px-2 sm:flex"
    >
      <Languages className="h-4 w-4" />
      <span className="text-[10px] font-semibold">{currentLabel}</span>
    </button>
  );
}
