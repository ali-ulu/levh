"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Loader2, ShieldQuestion } from "lucide-react";

export function AdmissionGate() {
    const t = useT();
    const [admissionContent, setAdmissionContent] = useState("");
    const [admissionBusy, setAdmissionBusy] = useState(false);
    const [admissionDecision, setAdmissionDecision] = useState<{
      action: string;
      reasons: string[];
      redacted: boolean;
      secrets: string[];
      redacted_content: string;
      max_similarity: number;
    } | null>(null);
    const [admissionError, setAdmissionError] = useState("");
    const runEvaluateAdmission = async () => {
      if (admissionBusy || !admissionContent.trim()) return;
      setAdmissionBusy(true);
      setAdmissionError("");
      setAdmissionDecision(null);
      try {
        const r = await api.evaluateAdmission(admissionContent);
        setAdmissionDecision(r.decision);
      } catch (e) {
        setAdmissionError(e instanceof Error ? e.message : t("settings.admissionGate.errorFallback"));
      }
      setAdmissionBusy(false);
    };

  return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <ShieldQuestion className="h-4 w-4" />
            {t("settings.admissionGate.title")}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-muted-foreground">
            {t("settings.admissionGate.description")}
          </p>
          <Textarea
            placeholder={t("settings.admissionGate.placeholder")}
            value={admissionContent}
            onChange={(e) => setAdmissionContent(e.target.value)}
            rows={3}
          />
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={runEvaluateAdmission}
              disabled={admissionBusy || !admissionContent.trim()}
            >
              {admissionBusy && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              {t("settings.admissionGate.evaluate")}
            </Button>
            {admissionError && (
              <span className="text-xs text-destructive">{admissionError}</span>
            )}
          </div>
          {admissionDecision && (
            <div className="space-y-2 pt-2 border-t">
              <div className="flex items-center gap-2">
                <Badge
                  variant={
                    admissionDecision.action === "admit"
                      ? "default"
                      : admissionDecision.action === "redact"
                      ? "secondary"
                      : admissionDecision.action === "review"
                      ? "outline"
                      : "destructive"
                  }
                >
                  {admissionDecision.action.toUpperCase()}
                </Badge>
                {admissionDecision.reasons.length > 0 && (
                  <span className="text-xs text-muted-foreground">
                    {admissionDecision.reasons.join(", ")}
                  </span>
                )}
              </div>
              {admissionDecision.redacted && (
                <div className="text-xs">
                  <span className="text-muted-foreground">{t("settings.admissionGate.redactedPreview")} </span>
                  <span className="font-mono bg-muted px-1 rounded">
                    {admissionDecision.redacted_content}
                  </span>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
  );
}
