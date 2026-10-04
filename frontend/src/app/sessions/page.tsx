"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type { Session } from "@/types";
import { History, Loader2, Plus, Sparkles, Square } from "lucide-react";

export default function SessionsPage() {
  const t = useT();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [endingId, setEndingId] = useState<string | null>(null);
  const [summarizingId, setSummarizingId] = useState<string | null>(null);
  const [summaryResult, setSummaryResult] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSessions(await api.listSessions(100));
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const create = async () => {
    if (!newName.trim() || creating) return;
    setCreating(true);
    try {
      await api.createSession(newName.trim());
      setNewName("");
      load();
    } catch {}
    setCreating(false);
  };

  const end = async (id: string) => {
    setEndingId(id);
    try {
      await api.endSession(id);
      load();
    } catch {}
    setEndingId(null);
  };

  const summarize = async (id: string) => {
    setSummarizingId(id);
    try {
      const r = await api.summarizeSession(id);
      setSummaryResult((prev) => ({
        ...prev,
        [id]: r.summarized
          ? t("app.sessions.summary.success", { id: `${r.summary!.id.slice(0, 8)}…` })
          : r.reason ?? t("app.sessions.summary.nothing"),
      }));
      load();
    } catch (e) {
      setSummaryResult((prev) => ({
        ...prev,
        [id]: e instanceof Error ? e.message : t("app.sessions.summary.failed"),
      }));
    }
    setSummarizingId(null);
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">{t("app.sessions.title")}</h1>
        <p className="text-sm text-muted-foreground mt-1">
          {t("app.sessions.subtitle")}
        </p>
      </div>

      <div className="flex gap-2 max-w-md">
        <Input
          placeholder={t("app.sessions.placeholder")}
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && create()}
        />
        <Button onClick={create} disabled={!newName.trim() || creating}>
          {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
        </Button>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : sessions.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground space-y-2">
            <History className="h-8 w-8 mx-auto opacity-40" />
            <p>{t("app.sessions.empty.beforeTool")}</p>
            <p>
              <code className="text-xs bg-muted px-1 rounded">create_session</code>{" "}
              {t("app.sessions.empty.afterTool")}
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {sessions.map((s) => (
            <Card key={s.id}>
              <CardContent className="p-3 flex items-center gap-3 flex-wrap">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm truncate">{s.name}</span>
                    <Badge
                      variant={s.status === "active" ? "default" : "secondary"}
                      className="text-[11px]"
                    >
                      {s.status}
                    </Badge>
                  </div>
                  <div className="text-xs text-muted-foreground mt-0.5">
                    {t(
                      s.memory_count === 1
                        ? "app.sessions.memoryCount.one"
                        : "app.sessions.memoryCount.other",
                      { count: s.memory_count },
                    )} · {t("app.sessions.started")}{" "}
                    {new Date(s.created_at).toLocaleString("en-GB")}
                    {s.ended_at && (
                      <> · {t("app.sessions.ended")} {new Date(s.ended_at).toLocaleString("en-GB")}</>
                    )}
                  </div>
                  {summaryResult[s.id] && (
                    <div className="text-xs text-muted-foreground mt-1 flex items-center gap-1">
                      <Sparkles className="h-3 w-3" />
                      {summaryResult[s.id]}
                    </div>
                  )}
                </div>
                <Button asChild variant="outline" size="sm">
                  <Link href={`/memories/?session=${encodeURIComponent(s.id)}`}>{t("app.sessions.memories")}</Link>
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => summarize(s.id)}
                  disabled={summarizingId === s.id || s.memory_count === 0}
                  title={t("app.sessions.summarizeTitle")}
                >
                  {summarizingId === s.id ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <>
                      <Sparkles className="h-3 w-3 mr-1.5" />
                      {t("app.sessions.summarize")}
                    </>
                  )}
                </Button>
                {s.status === "active" && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => end(s.id)}
                    disabled={endingId === s.id}
                  >
                    {endingId === s.id ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <>
                        <Square className="h-3 w-3 mr-1.5" />
                        {t("app.sessions.end")}
                      </>
                    )}
                  </Button>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
