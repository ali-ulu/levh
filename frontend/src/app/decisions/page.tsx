"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { Decision } from "@/types";
import { Gavel, Loader2 } from "lucide-react";

const RANGES = [
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
  { label: "1y", days: 365 },
];

export default function DecisionsPage() {
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(90);

  const load = useCallback(async (d: number) => {
    setLoading(true);
    try {
      setDecisions((await api.listDecisions(d)).decisions);
    } catch {
      setDecisions([]);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load(days);
  }, [load, days]);

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold">Decisions</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Decisions detected across your memories — &ldquo;we decided&rdquo;,
            &ldquo;agreed to&rdquo;, &ldquo;karar verdik&rdquo;. Fully offline, no
            manual tagging.
          </p>
        </div>
        <div className="flex gap-1 rounded-lg border p-0.5">
          {RANGES.map((r) => (
            <button
              key={r.days}
              onClick={() => setDays(r.days)}
              className={
                "px-3 py-1 text-xs rounded-md transition-colors " +
                (days === r.days
                  ? "bg-primary text-primary-foreground"
                  : "hover:bg-accent")
              }
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : decisions.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground space-y-2">
            <Gavel className="h-8 w-8 mx-auto opacity-40" />
            <p>No decisions detected in the last {days} days.</p>
            <p>
              Decisions are picked up automatically from memory content — meeting
              notes, transcripts, and captured discussions.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {decisions.map((d) => (
            <div key={d.id} className="border rounded-lg p-3 bg-card">
              <div className="flex items-start gap-2.5">
                <Gavel className="h-4 w-4 text-primary mt-0.5 shrink-0" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm">{d.text}</p>
                  <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                    <span className="text-[11px] text-muted-foreground">{d.date}</span>
                    {d.project && (
                      <Badge variant="outline" className="text-[11px]">
                        {d.project}
                      </Badge>
                    )}
                    {d.source && (
                      <Badge variant="secondary" className="text-[11px]">
                        {d.source.replace("connector:", "")}
                      </Badge>
                    )}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
