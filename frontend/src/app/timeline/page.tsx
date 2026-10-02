"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { MemoryDetailDrawer } from "@/components/memory-detail-drawer";
import type { Memory, TimelineDay } from "@/types";
import { CalendarClock, Clock, Loader2 } from "lucide-react";

function formatDay(day: string): string {
  try {
    const [y, m, d] = day.split("-").map(Number);
    // `en-GB`/`2-digit` are `Intl.DateTimeFormat` option values, not copy.
    return new Date(y, m - 1, d).toLocaleDateString("en-GB", {
      weekday: "long",
      day: "2-digit",
      month: "long",
      year: "numeric",
    });
  } catch {
    return day;
  }
}

export default function TimelinePage() {
  const t = useT();
  const [days, setDays] = useState<TimelineDay[]>([]);
  const [loading, setLoading] = useState(true);
  const [drawerMemory, setDrawerMemory] = useState<Memory | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setDays((await api.timeline(30)).timeline);
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

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">{t("app.timeline.title")}</h1>
        <p className="text-sm text-muted-foreground mt-1">
          {t("app.timeline.subtitle")}
        </p>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : days.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground space-y-2">
            <CalendarClock className="h-8 w-8 mx-auto opacity-40" />
            <p>{t("app.timeline.empty.title")}</p>
            <p>{t("app.timeline.empty.hint")}</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          {days.map((group) => (
            <div key={group.date} className="space-y-2">
              <div className="flex items-center gap-2 sticky top-0 bg-background/95 backdrop-blur py-1">
                <Clock className="h-4 w-4 text-muted-foreground" />
                <h2 className="text-sm font-semibold">{formatDay(group.date)}</h2>
                <Badge variant="secondary" className="text-[11px]">
                  {t(group.count === 1 ? "app.timeline.count.one" : "app.timeline.count.other", {
                    count: group.count,
                  })}
                </Badge>
              </div>
              <div className="space-y-2">
                {group.items.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => openItem(item.id)}
                    className="w-full text-left border rounded-lg p-3 bg-card hover:bg-accent/40 transition-colors"
                  >
                    <p className="text-sm line-clamp-2">{item.summary}</p>
                    <div className="flex items-center gap-1.5 mt-1.5">
                      {item.source && (
                        <Badge variant="outline" className="text-[11px]">
                          {item.source.replace("connector:", "")}
                        </Badge>
                      )}
                      <span className="text-[11px] text-muted-foreground">
                        {item.memory_type}
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {drawerMemory && (
        <MemoryDetailDrawer memory={drawerMemory} onClose={() => setDrawerMemory(null)} />
      )}
    </div>
  );
}
