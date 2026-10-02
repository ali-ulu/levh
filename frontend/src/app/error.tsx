"use client";

import { useEffect } from "react";
import { useT } from "@/lib/i18n";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useT();

  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="premium-card m-6 rounded-[22px] border p-6">
      <h2 className="text-base font-semibold">{t("app.error.title")}</h2>
      <p className="mt-2 whitespace-pre-wrap text-sm text-muted-foreground">
        {error.message || t("app.error.fallback")}
      </p>
      <button
        onClick={reset}
        className="mini-action mt-4"
      >
        {t("app.error.retry")}
      </button>
    </div>
  );
}
