"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MemoryDetailDrawer } from "@/components/memory-detail-drawer";
import type { Briefing, Memory } from "@/types";
import { AlertTriangle, CalendarClock, ListChecks, Loader2, Sunrise } from "lucide-react";

const EMPTY: Briefing = {
  generated_at: "",
  today: [],
  commitments: [],
  fading: [],
  counts: { today: 0, commitments: 0, fading: 0, recent_total: 0 },
};

export default function BriefingPage() {
  const [briefing, setBriefing] = useState<Briefing>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [drawerMemory, setDrawerMemory] = useState<Memory | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setBriefing((await api.briefing(7)).briefing);
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openItem = async (id: string) => {
    try {
      setDrawerMemory(await api.getMemory(id));
    } catch {}
  };

  const { today, commitments, fading, counts } = briefing;
  const isEmpty = today.length === 0 && commitments.length === 0 && fading.length === 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Daily Briefing</h1>
        <p className="text-sm text-muted-foreground mt-1">
          What&apos;s on today, what you recently committed to, and what
          you&apos;re about to forget — computed deterministically from your
          memories, no LLM required.
        </p>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : isEmpty ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground space-y-2">
            <Sunrise className="h-8 w-8 mx-auto opacity-40" />
            <p>Nothing pressing.</p>
            <p>{counts.recent_total} memories captured in the last 7 days.</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          {/* Today */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <CalendarClock className="h-4 w-4 text-muted-foreground" />
                Today
                <Badge variant="secondary" className="text-[11px] ml-auto">
                  {counts.today}
                </Badge>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {today.length === 0 ? (
                <p className="text-sm text-muted-foreground py-4 text-center">
                  Nothing scheduled or captured for today.
                </p>
              ) : (
                <div className="space-y-2">
                  {today.map((item) => (
                    <button
                      key={item.id}
                      onClick={() => openItem(item.id)}
                      className="w-full text-left border rounded-lg p-3 bg-card hover:bg-accent/40 transition-colors"
                    >
                      <div className="flex items-center gap-2">
                        {item.time && (
                          <span className="text-[11px] font-mono text-muted-foreground shrink-0">
                            {item.time}
                          </span>
                        )}
                        <p className="text-sm line-clamp-2 flex-1">{item.summary}</p>
                      </div>
                      {item.source && (
                        <Badge variant="outline" className="text-[11px] mt-1.5">
                          {item.source.replace("connector:", "")}
                        </Badge>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Open Commitments */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <ListChecks className="h-4 w-4 text-muted-foreground" />
                Open Commitments
                <Badge variant="secondary" className="text-[11px] ml-auto">
                  {counts.commitments}
                </Badge>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {commitments.length === 0 ? (
                <p className="text-sm text-muted-foreground py-4 text-center">
                  No open commitments detected in recent memories.
                </p>
              ) : (
                <div className="space-y-2">
                  {commitments.map((c) => (
                    <button
                      key={c.id}
                      onClick={() => openItem(c.id)}
                      className="w-full text-left border rounded-lg p-3 bg-card hover:bg-accent/40 transition-colors"
                    >
                      <p className="text-sm line-clamp-2">{c.text}</p>
                      <div className="flex items-center gap-1.5 mt-1.5">
                        {c.source && (
                          <Badge variant="outline" className="text-[11px]">
                            {c.source.replace("connector:", "")}
                          </Badge>
                        )}
                        <span className="text-[11px] text-muted-foreground">{c.date}</span>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Might Be Forgetting */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 text-muted-foreground" />
                Might Be Forgetting
                <Badge variant="secondary" className="text-[11px] ml-auto">
                  {counts.fading}
                </Badge>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {fading.length === 0 ? (
                <p className="text-sm text-muted-foreground py-4 text-center">
                  Nothing is fading — your memory is healthy.
                </p>
              ) : (
                <div className="space-y-2">
                  {fading.map((f) => (
                    <button
                      key={f.id}
                      onClick={() => openItem(f.id)}
                      className="w-full text-left border rounded-lg p-3 bg-card hover:bg-accent/40 transition-colors flex items-center gap-2"
                    >
                      <Badge
                        variant="outline"
                        className="shrink-0 text-[11px] font-mono border-red-500/50 text-red-600 dark:text-red-400"
                      >
                        {(f.retention * 100).toFixed(0)}%
                      </Badge>
                      <span className="flex-1 text-sm line-clamp-2 text-muted-foreground">
                        {f.summary}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {drawerMemory && (
        <MemoryDetailDrawer memory={drawerMemory} onClose={() => setDrawerMemory(null)} />
      )}
    </div>
  );
}
