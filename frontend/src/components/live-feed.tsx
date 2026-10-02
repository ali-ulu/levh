"use client";

import { useLiveEvents } from "@/lib/use-live-events";
import { useT } from "@/lib/i18n";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Radio } from "lucide-react";

type Translate = (key: string, vars?: Record<string, string | number>) => string;

const EVENT_LABELS: Record<string, { labelKey: string; className: string }> = {
  stored: { labelKey: "liveFeed.event.stored", className: "bg-green-500/15 text-green-600 dark:text-green-400" },
  updated: { labelKey: "liveFeed.event.updated", className: "bg-blue-500/15 text-blue-600 dark:text-blue-400" },
  deleted: { labelKey: "liveFeed.event.deleted", className: "bg-red-500/15 text-red-600 dark:text-red-400" },
  recalled: { labelKey: "liveFeed.event.recalled", className: "bg-violet-500/15 text-violet-600 dark:text-violet-400" },
  consolidated: { labelKey: "liveFeed.event.consolidated", className: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
  session_created: { labelKey: "liveFeed.event.sessionCreated", className: "bg-blue-500/15 text-blue-600 dark:text-blue-400" },
  session_ended: { labelKey: "liveFeed.event.sessionEnded", className: "bg-muted text-muted-foreground" },
  imported: { labelKey: "liveFeed.event.imported", className: "bg-green-500/15 text-green-600 dark:text-green-400" },
  interference: { labelKey: "liveFeed.event.interference", className: "bg-orange-500/15 text-orange-600 dark:text-orange-400" },
  asked: { labelKey: "liveFeed.event.asked", className: "bg-primary/15 text-primary" },
  session_summarized: { labelKey: "liveFeed.event.summarized", className: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
};

function describe(event: string, payload: Record<string, any>, t: Translate): string {
  switch (event) {
    case "stored":
    case "updated":
      return payload.content ? String(payload.content).slice(0, 90) : payload.id ?? "";
    case "deleted":
      return payload.id ?? "";
    case "recalled":
      return t("liveFeed.detail.recalled", { query: payload.query, count: payload.count });
    case "consolidated":
      return t("liveFeed.detail.promoted", { count: payload.count });
    case "session_created":
    case "session_ended":
      return payload.name ?? payload.id ?? "";
    case "imported":
      return t("liveFeed.detail.imported", { count: payload.count });
    case "interference":
      return t("liveFeed.detail.interference", { count: payload.weakened_ids?.length ?? 0 });
    case "asked":
      return t("liveFeed.detail.asked", { question: payload.question, count: payload.source_count });
    case "session_summarized":
      return t("liveFeed.detail.summarized", { count: payload.from_count });
    default:
      return "";
  }
}

export function LiveFeed() {
  const t = useT();
  const { events, connected } = useLiveEvents();

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Radio className={`h-4 w-4 ${connected ? "text-green-500" : "text-muted-foreground"}`} />
          {t("liveFeed.title")}
          <span className={`ml-auto text-xs font-normal ${connected ? "text-green-500" : "text-muted-foreground"}`}>
            {connected ? t("liveFeed.connected") : t("liveFeed.connecting")}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {events.length === 0 ? (
          <p role="status" aria-live="polite" className="text-sm text-muted-foreground py-6 text-center">
            {t("liveFeed.empty")}
          </p>
        ) : (
          <div
            role="log"
            aria-live="polite"
            aria-label={t("liveFeed.logLabel")}
            className="space-y-2 max-h-80 overflow-y-auto pr-1"
          >
            {events.map((e, i) => {
              const meta = EVENT_LABELS[e.event];
              const label = meta ? t(meta.labelKey) : e.event;
              const className = meta?.className ?? "bg-muted text-muted-foreground";
              return (
                <div key={`${e.receivedAt}-${i}`} className="flex items-start gap-2 text-sm">
                  <Badge className={`shrink-0 text-[11px] border-0 ${className}`}>{label}</Badge>
                  <span className="flex-1 text-muted-foreground truncate">{describe(e.event, e.payload, t)}</span>
                  <span className="shrink-0 text-[11px] text-muted-foreground/70">
                    {new Date(e.receivedAt).toLocaleTimeString("en-GB")}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
