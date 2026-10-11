"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { FederationPullResult } from "@/types";
import { ArrowDownToLine, Loader2 } from "lucide-react";

interface PullCounters {
  imported: number;
  redacted: number;
  duplicates: number;
  held: number;
  errors: number;
}

const COUNTER_KEYS = [
  ["imported", "settings.federation.imported"],
  ["redacted", "settings.federation.redacted"],
  ["duplicates", "settings.federation.duplicates"],
  ["held", "settings.federation.held"],
  ["errors", "settings.federation.errors"],
] as const;

export function Federation() {
  const t = useT();
  const [source, setSource] = useState("");
  const [expectedNode, setExpectedNode] = useState("");
  const [pullBusy, setPullBusy] = useState(false);
  const [pullResult, setPullResult] = useState<(FederationPullResult & PullCounters) | null>(null);
  const [pullError, setPullError] = useState("");

  const runPull = async () => {
    if (!source.trim()) {
      setPullError(t("settings.federation.emptySource"));
      return;
    }
    setPullBusy(true);
    setPullError("");
    setPullResult(null);
    try {
      setPullResult(await api.federationPull(source.trim(), expectedNode.trim() || undefined));
    } catch (e) {
      setPullError(e instanceof Error ? e.message : t("settings.federation.pullFailed"));
    }
    setPullBusy(false);
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <ArrowDownToLine className="h-4 w-4" />
          {t("settings.federation.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          {t("settings.federation.description")}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="url"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            placeholder={t("settings.federation.sourcePlaceholder")}
            className="border rounded-md px-3 py-2 text-sm w-full sm:w-80"
          />
          <input
            type="text"
            value={expectedNode}
            onChange={(e) => setExpectedNode(e.target.value)}
            placeholder={t("settings.federation.nodePlaceholder")}
            className="border rounded-md px-3 py-2 text-sm w-full sm:w-44"
          />
          <Button variant="outline" onClick={runPull} disabled={pullBusy}>
            {pullBusy && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            {t("settings.federation.pull")}
          </Button>
        </div>
        {pullError && <span className="text-xs text-destructive">{pullError}</span>}
        {pullResult && (
          <div className="space-y-2 pt-1">
            <p className="text-xs text-muted-foreground">
              {t("settings.federation.importedFrom", {
                node: pullResult.node_id ?? "—",
                algorithm: pullResult.algorithm ?? "—",
              })}
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
              {COUNTER_KEYS.map(([key, label]) => (
                <div key={key} className="rounded-lg border p-2.5 text-center">
                  <div className="text-lg font-bold tabular-nums">{pullResult[key]}</div>
                  <div className="text-[11px] text-muted-foreground">{t(label)}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

