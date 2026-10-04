"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { ConflictCandidate } from "@/types";
import {
  Ban,
  CheckCircle2,
  GitCompareArrows,
  Loader2,
  RefreshCw,
  Scale,
  ShieldCheck,
} from "lucide-react";

const ACTIONS: {
  action: string;
  labelKey: string;
  icon: typeof CheckCircle2;
  variant?: "outline" | "destructive";
}[] = [
  { action: "dismiss", labelKey: "app.conflicts.action.dismiss", icon: Ban, variant: "outline" },
  { action: "confirm", labelKey: "app.conflicts.action.confirm", icon: CheckCircle2, variant: "outline" },
  { action: "resolve_keep_a", labelKey: "app.conflicts.action.keepA", icon: ShieldCheck, variant: "outline" },
  { action: "resolve_keep_b", labelKey: "app.conflicts.action.keepB", icon: ShieldCheck, variant: "outline" },
  { action: "mark_both_valid", labelKey: "app.conflicts.action.bothValid", icon: Scale, variant: "outline" },
];

export default function ConflictsPage() {
  const t = useT();
  const [items, setItems] = useState<ConflictCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [detecting, setDetecting] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems((await api.listConflicts("open")).conflicts);
    } catch {
      setItems([]);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const detect = async () => {
    setDetecting(true);
    try {
      await api.detectConflicts();
      await load();
    } catch {
      /* surfaced on next load */
    }
    setDetecting(false);
  };

  const apply = async (id: string, action: string) => {
    setBusy(id + action);
    try {
      await api.reviewConflict(id, action);
      // A reviewed candidate leaves the open queue — drop it locally.
      setItems((prev) => prev.filter((i) => i.id !== id));
    } catch {
      /* keep card; surfaced on next load */
    }
    setBusy(null);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold">{t("app.conflicts.title")}</h1>
          <p className="text-sm text-muted-foreground mt-1">{t("app.conflicts.subtitle")}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            <RefreshCw className={"h-4 w-4 mr-1.5 " + (loading ? "animate-spin" : "")} />
            {t("app.conflicts.refresh")}
          </Button>
          <Button size="sm" onClick={detect} disabled={detecting}>
            {detecting ? (
              <Loader2 className="h-4 w-4 mr-1.5 animate-spin" />
            ) : (
              <GitCompareArrows className="h-4 w-4 mr-1.5" />
            )}
            {t("app.conflicts.detect")}
          </Button>
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : items.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground space-y-2">
            <GitCompareArrows className="h-8 w-8 mx-auto opacity-40" />
            <p>{t("app.conflicts.empty")}</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {items.map((it) => {
            const expl = it.explanation ?? {};
            return (
              <Card key={it.id}>
                <CardContent className="p-4 space-y-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Badge variant="secondary" className="text-[11px]">
                        {it.signal_type}
                        {expl.detail ? `: ${expl.detail}` : ""}
                      </Badge>
                      <Badge variant="outline" className="text-[11px]">
                        {t("app.conflicts.confidence", { percent: (it.confidence * 100).toFixed(0) })}
                      </Badge>
                      {it.shared_entities.map((e) => (
                        <Badge key={e} variant="outline" className="text-[11px]">
                          {e}
                        </Badge>
                      ))}
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
                      <div className="rounded-md border p-2">
                        <p className="text-[11px] text-muted-foreground mb-1">
                          {t("app.conflicts.sourceA", { source: (expl.a_source ?? "").replace("connector:", "") || t("app.conflicts.unknownSource") })}
                        </p>
                        <p className="text-sm">{expl.a_preview}</p>
                      </div>
                      <div className="rounded-md border p-2">
                        <p className="text-[11px] text-muted-foreground mb-1">
                          {t("app.conflicts.sourceB", { source: (expl.b_source ?? "").replace("connector:", "") || t("app.conflicts.unknownSource") })}
                        </p>
                        <p className="text-sm">{expl.b_preview}</p>
                      </div>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {ACTIONS.map((a) => (
                      <Button
                        key={a.action}
                        size="sm"
                        variant={a.variant ?? "outline"}
                        disabled={busy === it.id + a.action}
                        onClick={() => apply(it.id, a.action)}
                      >
                        {busy === it.id + a.action ? (
                          <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
                        ) : (
                          <a.icon className="h-3.5 w-3.5 mr-1" />
                        )}
                        {t(a.labelKey)}
                      </Button>
                    ))}
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
