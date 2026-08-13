"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { TrustBreakdown } from "@/types";
import { BadgeCheck, Loader2 } from "lucide-react";

export function TrustProvenance() {
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
        setTrustError(e instanceof Error ? e.message : "Recompute failed");
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
        setTrustError(e instanceof Error ? e.message : "Low-trust lookup failed");
      }
      setLowTrustBusy(false);
    };

  return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <BadgeCheck className="h-4 w-4" />
            Trust &amp; provenance
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-muted-foreground">
            A deterministic, explainable provenance signal — where a memory came from, how many
            independent sources corroborate it, and whether it carries risk flags. This is not a
            truth score, and it never changes recall ranking.
          </p>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={runRecomputeTrust} disabled={trustBusy}>
              {trustBusy && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Recompute trust scores
            </Button>
            {trustError && <span className="text-xs text-destructive">{trustError}</span>}
          </div>
          {trustScored !== null && trustByLabel && (
            <div className="space-y-2 pt-2 border-t">
              <p className="text-xs text-muted-foreground">Scored {trustScored} memories.</p>
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
              <p className="text-xs text-muted-foreground">Lowest-trust memories</p>
              <ul className="space-y-1">
                {lowTrust.map((t) => (
                  <li key={t.memory_id} className="text-xs text-muted-foreground">
                    <Badge variant="secondary" className="text-[11px]">
                      {t.label}
                    </Badge>{" "}
                    <span className="font-mono">{t.confidence.toFixed(2)}</span>{" "}
                    <span className="font-mono">{t.memory_id.slice(0, 8)}</span> —{" "}
                    {t.evidence?.source ?? "unknown"} source
                  </li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>
  );
}
