"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { ReviewAction, ReviewItem } from "@/types";
import {
  ArrowUpCircle,
  Clock8,
  Loader2,
  Pin,
  RefreshCw,
  ThumbsUp,
  Trash2,
  TrendingDown,
} from "lucide-react";

const ACTIONS: {
  action: ReviewAction;
  labelKey: string;
  icon: typeof ThumbsUp;
  variant?: "outline" | "destructive";
}[] = [
  { action: "keep", labelKey: "app.review.action.keep", icon: ThumbsUp, variant: "outline" },
  { action: "reinforce", labelKey: "app.review.action.reinforce", icon: ArrowUpCircle, variant: "outline" },
  { action: "weaken", labelKey: "app.review.action.weaken", icon: TrendingDown, variant: "outline" },
  { action: "pin", labelKey: "app.review.action.pin", icon: Pin, variant: "outline" },
  { action: "snooze", labelKey: "app.review.action.snooze", icon: Clock8, variant: "outline" },
  { action: "forget", labelKey: "app.review.action.forget", icon: Trash2, variant: "destructive" },
];

export default function ReviewPage() {
  const t = useT();
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems((await api.reviewQueue(0.5)).review);
    } catch {
      setItems([]);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const apply = async (id: string, action: ReviewAction) => {
    setBusy(id + action);
    try {
      await api.reviewMemory(id, action);
      // The item leaves the queue after any decision — drop it locally.
      setItems((prev) => prev.filter((i) => i.id !== id));
    } catch {
      /* keep item; surfaced on next load */
    }
    setBusy(null);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold">{t("app.review.title")}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {t("app.review.subtitle")}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <RefreshCw className={"h-4 w-4 mr-1.5 " + (loading ? "animate-spin" : "")} />
          {t("app.review.refresh")}
        </Button>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : items.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground space-y-2">
            <ThumbsUp className="h-8 w-8 mx-auto opacity-40" />
            <p>{t("app.review.empty.title")}</p>
            <p>{t("app.review.empty.hint")}</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {items.map((it) => (
            <Card key={it.id}>
              <CardContent className="p-4 space-y-3">
                <div>
                  <p className="text-sm">{it.content}</p>
                  <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
                    <Badge variant="secondary" className="text-[11px]">
                      {t("app.review.retention", {
                        percent: (it.retention * 100).toFixed(0),
                      })}
                    </Badge>
                    {it.project && (
                      <Badge variant="outline" className="text-[11px]">
                        {it.project}
                      </Badge>
                    )}
                    {it.source && (
                      <Badge variant="outline" className="text-[11px]">
                        {it.source.replace("connector:", "")}
                      </Badge>
                    )}
                    <span className="text-[11px] text-muted-foreground">
                      {t("app.review.history", {
                        recalls: it.recall_count,
                        reviews: it.review_count,
                      })}
                    </span>
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-1">{it.reason}</p>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {ACTIONS.map((a) => (
                    <Button
                      key={a.action}
                      size="sm"
                      variant={a.variant ?? "outline"}
                      disabled={busy === it.id + a.action}
                      onClick={() => apply(it.id, a.action)}
                    >
                      {busy === it.id + a.action ? (
                        <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
                      ) : (
                        <a.icon className="h-3.5 w-3.5 mr-1" />
                      )}
                      {t(a.labelKey)}
                    </Button>
                  ))}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
