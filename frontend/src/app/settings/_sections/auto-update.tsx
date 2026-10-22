"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { AutoUpdateStatus } from "@/types";
import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";

// Auto-update can stop applying updates without anything failing: a checkout
// parked on another branch, local edits, or a diverged history all make the
// supervisor skip forever while the server keeps serving stale code (#522).
// That skip used to live only in the supervisor's log, so this section is the
// one place a dashboard user can see it — with the command that fixes it.
export function AutoUpdate() {
  const t = useT();
  const [status, setStatus] = useState<AutoUpdateStatus | null | undefined>(
    undefined,
  );

  useEffect(() => {
    api
      .health()
      .then((body) => setStatus(body.auto_update ?? null))
      .catch(() => setStatus(null));
  }, []);

  // No supervisor ran, or this install predates the state file: say nothing
  // rather than imply a problem where there is none.
  if (status === null) return null;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <RefreshCw className="h-4 w-4" />
          {t("settings.autoUpdate.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {status === undefined ? (
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        ) : (
          <>
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">
                {t("settings.autoUpdate.status")}
              </span>
              <Badge
                variant={status.needs_attention ? "destructive" : "secondary"}
                className="text-[11px]"
              >
                {status.status}
              </Badge>
              {status.behind > 0 && (
                <span className="text-xs text-muted-foreground">
                  {t("settings.autoUpdate.behind", { count: status.behind })}
                </span>
              )}
            </div>
            {status.reason && (
              <p className="flex items-start gap-2 text-xs">
                {status.needs_attention && (
                  <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0 text-destructive" />
                )}
                <span>{status.reason}</span>
              </p>
            )}
            {status.action && (
              <p className="text-xs">
                {t("settings.autoUpdate.action")}{" "}
                <code className="bg-muted px-1 rounded">{status.action}</code>
              </p>
            )}
            <p className="text-xs text-muted-foreground">
              {t("settings.autoUpdate.help")}
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
