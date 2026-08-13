"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { GuardRule, GuardViolation } from "@/types";
import { Loader2, RefreshCw, ShieldAlert } from "lucide-react";

const SEVERITIES = ["", "low", "medium", "high", "critical"];

// Severity is the one thing worth colouring here: it is what decides whether a
// rule outranks the rest when a context file can only carry a few.
const SEVERITY_STYLE: Record<string, string> = {
  low: "bg-muted text-muted-foreground",
  medium: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  high: "bg-orange-500/15 text-orange-700 dark:text-orange-400",
  critical: "bg-red-500/15 text-red-700 dark:text-red-400",
};

function severityBadge(severity: string) {
  return (
    <Badge
      variant="secondary"
      className={`text-[11px] ${SEVERITY_STYLE[severity] ?? SEVERITY_STYLE.medium}`}
    >
      {severity}
    </Badge>
  );
}

export default function GuardPage() {
  const [rules, setRules] = useState<GuardRule[]>([]);
  const [violations, setViolations] = useState<GuardViolation[]>([]);
  const [severity, setSeverity] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [r, v] = await Promise.all([
        api.listGuardRules(),
        api.listGuardViolations(severity || undefined),
      ]);
      setRules(r.rules);
      setViolations(v.violations);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the guard log");
      setRules([]);
      setViolations([]);
    }
    setLoading(false);
  }, [severity]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold">Mistake guard</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Mistakes recorded through the <code>record_mistake</code> tool. Each one
            becomes a pinned rule — pinned memories never decay — and leads the
            generated context file, so a later session reads it before working.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <RefreshCw className={"h-4 w-4 mr-1.5 " + (loading ? "animate-spin" : "")} />
          Refresh
        </Button>
      </div>

      {error && (
        <Card>
          <CardContent className="py-4 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : rules.length === 0 && violations.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground space-y-2">
            <ShieldAlert className="h-8 w-8 mx-auto opacity-40" />
            <p>
              No mistakes recorded yet. Call <code>record_mistake</code> from your AI
              client when a mistake has been identified and corrected.
            </p>
          </CardContent>
        </Card>
      ) : (
        <>
          <section className="space-y-2">
            <h2 className="text-sm font-semibold">
              Active rules{rules.length ? ` (${rules.length})` : ""}
            </h2>
            {rules.length === 0 ? (
              <p className="text-sm text-muted-foreground">No rules yet.</p>
            ) : (
              rules.map((rule) => (
                <Card key={rule.id}>
                  <CardContent className="p-4 space-y-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                      {severityBadge(rule.severity)}
                      {rule.project && (
                        <Badge variant="outline" className="text-[11px]">
                          {rule.project}
                        </Badge>
                      )}
                      <Badge variant="outline" className="text-[11px]">
                        {rule.created_at.slice(0, 10)}
                      </Badge>
                    </div>
                    <p className="text-sm font-medium">{rule.statement}</p>
                    {rule.task && (
                      <p className="text-xs text-muted-foreground">While: {rule.task}</p>
                    )}
                  </CardContent>
                </Card>
              ))
            )}
          </section>

          <section className="space-y-2">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <h2 className="text-sm font-semibold">
                Incidents{violations.length ? ` (${violations.length})` : ""}
              </h2>
              <div className="flex gap-1.5">
                {SEVERITIES.map((s) => (
                  <Button
                    key={s || "all"}
                    size="sm"
                    variant={severity === s ? "default" : "outline"}
                    onClick={() => setSeverity(s)}
                  >
                    {s || "all"}
                  </Button>
                ))}
              </div>
            </div>
            {violations.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No incidents{severity ? ` at ${severity} severity` : ""}.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-muted-foreground border-b">
                      <th className="py-2 pr-3 font-medium">When</th>
                      <th className="py-2 pr-3 font-medium">Severity</th>
                      <th className="py-2 pr-3 font-medium">What happened</th>
                      <th className="py-2 pr-3 font-medium">Tool</th>
                      <th className="py-2 font-medium">Source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {violations.map((v) => (
                      <tr key={v.id} className="border-b last:border-0 align-top">
                        <td className="py-2 pr-3 whitespace-nowrap text-muted-foreground">
                          {v.occurred_at.slice(0, 10)}
                        </td>
                        <td className="py-2 pr-3">{severityBadge(v.severity)}</td>
                        <td className="py-2 pr-3">
                          {v.wrong_action}
                          {v.task && (
                            <span className="block text-xs text-muted-foreground">
                              while: {v.task}
                            </span>
                          )}
                        </td>
                        <td className="py-2 pr-3 text-muted-foreground">
                          {v.tool_name || "—"}
                        </td>
                        <td className="py-2 text-muted-foreground">{v.source}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
