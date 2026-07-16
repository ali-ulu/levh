"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Memory } from "@/types";
import { BatteryCharging, HeartCrack, Loader2, Trash2 } from "lucide-react";

type FadingMemory = Memory & { retention: number };

export function FadingMemories({ onChanged }: { onChanged?: () => void }) {
  const [fading, setFading] = useState<FadingMemory[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .listFading(0.35, 6)
      .then(setFading)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    const iv = setInterval(load, 60000);
    return () => clearInterval(iv);
  }, [load]);

  const rescue = async (id: string) => {
    setBusyId(id);
    try {
      await api.reinforceMemory(id);
      load();
      onChanged?.();
    } catch {}
    setBusyId(null);
  };

  const letGo = async (m: FadingMemory) => {
    if (!confirm(`Forget this memory permanently?\n\n"${m.content.slice(0, 80)}"`)) return;
    setBusyId(m.id);
    try {
      await api.deleteMemory(m.id);
      load();
      onChanged?.();
    } catch {}
    setBusyId(null);
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <HeartCrack className="h-4 w-4 text-muted-foreground" />
          Fading Memories
          {fading.length > 0 && (
            <Badge variant="secondary" className="text-[11px] ml-auto">
              {fading.length} need review
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="flex justify-center py-4">
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          </div>
        ) : fading.length === 0 ? (
          <p className="text-sm text-muted-foreground py-4 text-center">
            Nothing is fading — your memory is healthy. Memories appear here when
            their predicted retention drops below 35%.
          </p>
        ) : (
          <div className="space-y-2">
            {fading.map((m) => (
              <div key={m.id} className="flex items-center gap-2 text-sm border rounded-lg p-2">
                <Badge
                  variant="outline"
                  className="shrink-0 text-[11px] font-mono border-red-500/50 text-red-600 dark:text-red-400"
                >
                  {(m.retention * 100).toFixed(0)}%
                </Badge>
                <span className="flex-1 truncate text-muted-foreground">{m.content}</span>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 shrink-0"
                  onClick={() => rescue(m.id)}
                  disabled={busyId === m.id}
                  aria-label="Reinforce — keep this memory"
                  title="Reinforce — keep this memory"
                >
                  {busyId === m.id ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <BatteryCharging className="h-3.5 w-3.5" />
                  )}
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 shrink-0"
                  onClick={() => letGo(m)}
                  disabled={busyId === m.id}
                  aria-label="Forget permanently"
                  title="Forget permanently"
                >
                  <Trash2 className="h-3.5 w-3.5 text-destructive" />
                </Button>
              </div>
            ))}
            <p className="text-[11px] text-muted-foreground pt-1">
              Rescue what still matters; let the rest fade. This is your memory
              curating itself.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
