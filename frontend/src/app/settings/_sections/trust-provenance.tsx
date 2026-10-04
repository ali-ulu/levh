"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { TrustBreakdown } from "@/types";
import { BadgeCheck, Loader2 } from "lucide-react";

export function TrustProvenance() {
    const t = useT();
    const [trustBusy, setTrustBusy] = useState(false);
    const [trustByLabel, setTrustByLabel] = useState<Record<string, number> | null>(null);
    const [trustScored, setTrustScored] = useState<number | null>(null);
    const [trustError, setTrustError] = useState("");
    const [lowTrustBusy, setLowTrustBusy] = useState(false);
    const [lowTrust, setLowTrust] = useState<TrustBreakdown[] | null>(null);
    const runRecomputeTrust = async () => {
      if (trustBusy) return;
      setTrustBusy(true);
      setTrustError("");
      try {
        const r = await api.recomputeTrust();
        setTrustScored(r.scored);
        setTrustByLabel(r.by_label);
        await runLowTrust();
      } catch (e) {
        setTrustError(e instanceof Error ? e.message : t("settings.trustProvenance.recomputeFailed"));
      }
      setTrustBusy(false);
    };
    const runLowTrust = async () => {
      if (lowTrustBusy) return;
      setLowTrustBusy(true);
      try {
        const r = await api.lowTrust(0.4, 10);
        setLowTrust(r.low_trust);
      } catch (e) {
        setTrustError(e instanceof Error ? e.message : t("settings.trustProvenance.lowTrustFailed"));
      }
      setLowTrustBusy(false);
    };

  return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <BadgeCheck className="h-4 w-4" />
            {t("settings.trustProvenance.title")}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-muted-foreground">
            {t("settings.trustProvenance.description")}
          </p>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={runRecomputeTrust} disabled={trustBusy}>
              {trustBusy && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              {t("settings.trustProvenance.recompute")}
            </Button>
            {trustError && <span className="text-xs text-destructive">{trustError}</span>}
          </div>
          {trustScored !== null && trustByLabel && (
            <div className="space-y-2 pt-2 border-t">
              <p className="text-xs text-muted-foreground">{t("settings.trustProvenance.scored", { count: trustScored })}</p>
              <div className="flex flex-wrap gap-2">
                {Object.entries(trustByLabel).map(([label, count]) => (
                  <Badge key={label} variant="secondary" className="text-[11px]">
                    {label}: {count}
                  </Badge>
                ))}
              </div>
            </div>
          )}
          {lowTrust && lowTrust.length > 0 && (
            <div className="space-y-2 pt-2 border-t">
              <p className="text-xs text-muted-foreground">{t("settings.trustProvenance.lowestTrust")}</p>
              <ul className="space-y-1">
                {lowTrust.map((entry) => (
                  <li key={entry.memory_id} className="text-xs text-muted-foreground">
                    <Badge variant="secondary" className="text-[11px]">
                      {entry.label}
                    </Badge>{" "}
                    <span className="font-mono">{entry.confidence.toFixed(2)}</span>{" "}
                    <span className="font-mono">{entry.memory_id.slice(0, 8)}</span> —{" "}
                    {t("settings.trustProvenance.sourceLabel", {
                      source:
                        entry.evidence?.source ??
                        t("settings.trustProvenance.unknownSource"),
                    })}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>
  );
}
