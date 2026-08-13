"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { BenchmarkResult } from "@/types";
import type { ServerConfig } from "@/types";
import { Gauge, Loader2 } from "lucide-react";

interface RecallQualityProps {
  config: ServerConfig | null;
}

export function RecallQuality({ config }: RecallQualityProps) {
    const [benchmarkBusy, setBenchmarkBusy] = useState(false);
    const [benchmarkResult, setBenchmarkResult] = useState<BenchmarkResult | null>(null);
    const [benchmarkError, setBenchmarkError] = useState("");
    const runBenchmark = async () => {
      setBenchmarkBusy(true);
      setBenchmarkError("");
      try {
        setBenchmarkResult(await api.runBenchmark(config?.embedder_mode));
      } catch (e) {
        setBenchmarkError(e instanceof Error ? e.message : "Benchmark failed");
      }
      setBenchmarkBusy(false);
    };

  return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Gauge className="h-4 w-4" />
            Recall Quality
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-muted-foreground">
            Runs a labelled query set against an isolated temp database (never touches your
            real memories) and reports hit@k / MRR — the same kind of accuracy metric managed
            memory services publish. Uses your configured embedder (
            <strong className="text-foreground">{config?.embedder_mode ?? "…"}</strong>
            {config?.embedder_mode === "hash" && " — non-semantic, treat as a plumbing check only"}
            ).
          </p>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={runBenchmark} disabled={benchmarkBusy}>
              {benchmarkBusy && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Run benchmark
            </Button>
            {benchmarkError && <span className="text-xs text-destructive">{benchmarkError}</span>}
          </div>
          {benchmarkResult && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-1">
              {(
                [
                  ["hit@1", benchmarkResult["hit@1"]],
                  ["hit@3", benchmarkResult["hit@3"]],
                  ["hit@5", benchmarkResult["hit@5"]],
                  ["MRR", benchmarkResult.mrr],
                ] as const
              ).map(([label, value]) => (
                <div key={label} className="rounded-lg border p-2.5 text-center">
                  <div className="text-lg font-bold tabular-nums">{(value * 100).toFixed(0)}%</div>
                  <div className="text-[11px] text-muted-foreground">{label}</div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
  );
}
