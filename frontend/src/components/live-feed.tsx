"use client";

import { useLiveEvents } from "@/lib/use-live-events";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Radio } from "lucide-react";

const EVENT_LABELS: Record<string, { label: string; className: string }> = {
  stored: { label: "stored", className: "bg-green-500/15 text-green-600 dark:text-green-400" },
  updated: { label: "updated", className: "bg-blue-500/15 text-blue-600 dark:text-blue-400" },
  deleted: { label: "deleted", className: "bg-red-500/15 text-red-600 dark:text-red-400" },
  recalled: { label: "recalled", className: "bg-violet-500/15 text-violet-600 dark:text-violet-400" },
  consolidated: { label: "consolidated", className: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
  session_created: { label: "session started", className: "bg-blue-500/15 text-blue-600 dark:text-blue-400" },
  session_ended: { label: "session ended", className: "bg-muted text-muted-foreground" },
  imported: { label: "imported", className: "bg-green-500/15 text-green-600 dark:text-green-400" },
  interference: { label: "superseded", className: "bg-orange-500/15 text-orange-600 dark:text-orange-400" },
  asked: { label: "asked", className: "bg-primary/15 text-primary" },
  session_summarized: { label: "summarized", className: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
};

function describe(event: string, payload: Record<string, any>): string {
  switch (event) {
    case "stored":
    case "updated":
      return payload.content ? String(payload.content).slice(0, 90) : payload.id ?? "";
    case "deleted":
      return payload.id ?? "";
    case "recalled":
      return `"${payload.query}" → ${payload.count} result${payload.count === 1 ? "" : "s"}`;
    case "consolidated":
      return `${payload.count} memories promoted to episodic`;
    case "session_created":
    case "session_ended":
      return payload.name ?? payload.id ?? "";
    case "imported":
      return `${payload.count} memories`;
    case "interference":
      return `new memory weakened ${payload.weakened_ids?.length ?? 0} older near-duplicate(s)`;
    case "asked":
      return `"${payload.question}" → ${payload.source_count} source${payload.source_count === 1 ? "" : "s"}`;
    case "session_summarized":
      return `session distilled from ${payload.from_count} memories`;
    default:
      return "";
  }
}

export function LiveFeed() {
  const { events, connected } = useLiveEvents();

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Radio className={`h-4 w-4 ${connected ? "text-green-500" : "text-muted-foreground"}`} />
          Live Activity
          <span className={`ml-auto text-xs font-normal ${connected ? "text-green-500" : "text-muted-foreground"}`}>
            {connected ? "● live" : "connecting..."}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {events.length === 0 ? (
          <p className="text-sm text-muted-foreground py-6 text-center">
            Waiting for activity — memories stored or recalled by any connected AI
            client will appear here in real time.
          </p>
        ) : (
          <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
            {events.map((e, i) => {
              const meta = EVENT_LABELS[e.event] ?? {
                label: e.event,
                className: "bg-muted text-muted-foreground",
              };
              return (
                <div key={`${e.receivedAt}-${i}`} className="flex items-start gap-2 text-sm">
                  <Badge className={`shrink-0 text-[11px] border-0 ${meta.className}`}>{meta.label}</Badge>
                  <span className="flex-1 text-muted-foreground truncate">{describe(e.event, e.payload)}</span>
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
