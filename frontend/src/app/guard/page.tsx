"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { GuardRule, GuardViolation } from "@/types";
import { Loader2, RefreshCw, ShieldAlert } from "lucide-react";

const GUARD_TOOL = "record_mistake";
const SEVERITIES = ["", "low", "medium", "high", "critical"];
const SEVERITY_LABEL_KEYS: Record<string, string> = {
  low: "app.guard.severity.low",
  medium: "app.guard.severity.medium",
  high: "app.guard.severity.high",
  critical: "app.guard.severity.critical",
};

// Severity is the one thing worth colouring here: it is what decides whether a
// rule outranks the rest when a context file can only carry a few.
const SEVERITY_STYLE: Record<string, string> = {
  low: "bg-muted text-muted-foreground",
  medium: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  high: "bg-orange-500/15 text-orange-700 dark:text-orange-400",
  critical: "bg-red-500/15 text-red-700 dark:text-red-400",
};

function severityBadge(severity: string, label: string) {
  return (
    <Badge
      variant="secondary"
      className={`text-[11px] ${SEVERITY_STYLE[severity] ?? SEVERITY_STYLE.medium}`}
    >
      {label}
    </Badge>
  );
}

export default function GuardPage() {
  const t = useT();
  const severityLabel = (value: string) => {
    const key = SEVERITY_LABEL_KEYS[value];
    return key ? t(key) : value;
  };
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
      setError(e instanceof Error ? e.message : t("app.guard.loadFailed"));
      setRules([]);
      setViolations([]);
    }
    setLoading(false);
  }, [severity, t]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold">{t("app.guard.title")}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {t("app.guard.subtitle.beforeTool")} <code>{GUARD_TOOL}</code>{" "}
            {t("app.guard.subtitle.afterTool")}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <RefreshCw className={"h-4 w-4 mr-1.5 " + (loading ? "animate-spin" : "")} />
          {t("app.guard.refresh")}
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
              {t("app.guard.empty.beforeTool")} <code>{GUARD_TOOL}</code>{" "}
              {t("app.guard.empty.afterTool")}
            </p>
          </CardContent>
        </Card>
      ) : (
        <>
          <section className="space-y-2">
            <h2 className="text-sm font-semibold">
              {rules.length
                ? t("app.guard.rules.withCount", { count: rules.length })
                : t("app.guard.rules.title")}
            </h2>
            {rules.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("app.guard.rules.none")}</p>
            ) : (
              rules.map((rule) => (
                <Card key={rule.id}>
                  <CardContent className="p-4 space-y-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                      {severityBadge(rule.severity, severityLabel(rule.severity))}
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
                      <p className="text-xs text-muted-foreground">{t("app.guard.while", { task: rule.task })}</p>
                    )}
                  </CardContent>
                </Card>
              ))
            )}
          </section>

          <section className="space-y-2">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <h2 className="text-sm font-semibold">
                {violations.length
                  ? t("app.guard.incidents.withCount", { count: violations.length })
                  : t("app.guard.incidents.title")}
              </h2>
              <div className="flex gap-1.5">
                {SEVERITIES.map((s) => (
                  <Button
                    key={s || "all"}
                    size="sm"
                    variant={severity === s ? "default" : "outline"}
                    onClick={() => setSeverity(s)}
                  >
                    {s ? severityLabel(s) : t("app.guard.severity.all")}
                  </Button>
                ))}
              </div>
            </div>
            {violations.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {severity
                  ? t("app.guard.incidents.noneSeverity", {
                      severity: severityLabel(severity),
                    })
                  : t("app.guard.incidents.none")}
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-muted-foreground border-b">
                      <th className="py-2 pr-3 font-medium">{t("app.guard.table.when")}</th>
                      <th className="py-2 pr-3 font-medium">{t("app.guard.table.severity")}</th>
                      <th className="py-2 pr-3 font-medium">{t("app.guard.table.whatHappened")}</th>
                      <th className="py-2 pr-3 font-medium">{t("app.guard.table.tool")}</th>
                      <th className="py-2 font-medium">{t("app.guard.table.source")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {violations.map((v) => (
                      <tr key={v.id} className="border-b last:border-0 align-top">
                        <td className="py-2 pr-3 whitespace-nowrap text-muted-foreground">
                          {v.occurred_at.slice(0, 10)}
                        </td>
                        <td className="py-2 pr-3">{severityBadge(v.severity, severityLabel(v.severity))}</td>
                        <td className="py-2 pr-3">
                          {v.wrong_action}
                          {v.task && (
                            <span className="block text-xs text-muted-foreground">
                              {t("app.guard.whileLower", { task: v.task })}
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
