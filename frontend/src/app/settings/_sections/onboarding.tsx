"use client";

// Reset the onboarding flag (#565). The card is dismissed once the readiness
// checks pass, and this is the way back: clearing the flag makes the
// walkthrough show again on the dashboard.
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useT } from "@/lib/i18n";
import { isOnboardingDismissed, resetOnboarding } from "@/lib/onboarding";
import { RotateCcw, Sparkles } from "lucide-react";

export function Onboarding() {
  const t = useT();
  // Read after mount: this is a client component in a static export, so the
  // prerender has no localStorage.
  const [dismissed, setDismissed] = useState<boolean | null>(null);

  useEffect(() => {
    setDismissed(isOnboardingDismissed());
  }, []);

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Sparkles className="h-4 w-4" />
          {t("settings.onboarding.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">{t("settings.onboarding.description")}</p>
        <Button
          variant="outline"
          onClick={() => {
            resetOnboarding();
            setDismissed(isOnboardingDismissed());
          }}
        >
          <RotateCcw className="mr-2 h-4 w-4" />
          {t("settings.onboarding.reset")}
        </Button>
        {dismissed !== null && (
          <p className="text-xs text-muted-foreground">
            {dismissed ? t("settings.onboarding.stateDone") : t("settings.onboarding.statePending")}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
