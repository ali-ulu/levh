"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { MemoryDetailDrawer } from "@/components/memory-detail-drawer";
import type { Memory, Person } from "@/types";
import {
  ArrowLeft,
  CalendarDays,
  Loader2,
  Mail,
  Mic,
  Search,
  Sparkles,
  Users,
} from "lucide-react";

const SOURCE_ICON: Record<string, typeof Mail> = {
  "connector:email": Mail,
  "connector:calendar": CalendarDays,
  "connector:transcript": Mic,
};

function SourceChips({ sources }: { sources: string[] }) {
  return (
    <div className="flex items-center gap-1">
      {sources.map((s) => {
        const Icon = SOURCE_ICON[s] ?? Users;
        return (
          <span key={s} title={s.replace("connector:", "")}>
            <Icon className="h-3 w-3 text-muted-foreground" />
          </span>
        );
      })}
    </div>
  );
}

export default function PeoplePage() {
  const [people, setPeople] = useState<Person[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");

  const [selected, setSelected] = useState<Person | null>(null);
  const [personMemories, setPersonMemories] = useState<Memory[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [answer, setAnswer] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [drawerMemory, setDrawerMemory] = useState<Memory | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setPeople((await api.listPeople()).people);
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openPerson = async (p: Person) => {
    setSelected(p);
    setAnswer(null);
    setDetailLoading(true);
    try {
      setPersonMemories((await api.getPerson(p.key)).memories);
    } catch {
      setPersonMemories([]);
    }
    setDetailLoading(false);
  };

  const askAboutPerson = async () => {
    if (!selected) return;
    setAsking(true);
    setAnswer(null);
    try {
      const q = `Summarize everything about ${selected.name}: what we discussed and any open items.`;
      setAnswer((await api.ask(q, 8)).answer);
    } catch (e) {
      setAnswer(e instanceof Error ? e.message : "Ask failed");
    }
    setAsking(false);
  };

  const filtered = people.filter(
    (p) =>
      !filter ||
      p.name.toLowerCase().includes(filter.toLowerCase()) ||
      (p.email ?? "").toLowerCase().includes(filter.toLowerCase())
  );

  // ── Detail view ──────────────────────────────────────────────────
  if (selected) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => setSelected(null)}>
          <ArrowLeft className="h-4 w-4 mr-1.5" />
          All people
        </Button>

        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-2xl font-bold">{selected.name}</h1>
            <p className="text-sm text-muted-foreground mt-0.5">
              {selected.email && <span className="font-mono">{selected.email} · </span>}
              {selected.memory_count} memories
              {selected.last_seen && <> · last seen {selected.last_seen.slice(0, 10)}</>}
            </p>
          </div>
          <Button onClick={askAboutPerson} disabled={asking}>
            {asking ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Sparkles className="h-4 w-4 mr-2" />}
            Ask about {selected.name.split(" ")[0]}
          </Button>
        </div>

        {answer && (
          <Card>
            <CardContent className="p-4 text-sm leading-relaxed whitespace-pre-wrap">{answer}</CardContent>
          </Card>
        )}

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Memories mentioning {selected.name.split(" ")[0]}</CardTitle>
          </CardHeader>
          <CardContent>
            {detailLoading ? (
              <div className="flex justify-center py-8">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : personMemories.length === 0 ? (
              <p className="text-sm text-muted-foreground py-4 text-center">No memories.</p>
            ) : (
              <div className="space-y-2">
                {personMemories.map((m) => (
                  <button
                    key={m.id}
                    onClick={() => setDrawerMemory(m)}
                    className="w-full text-left border rounded-lg p-3 hover:bg-accent/40 transition-colors"
                  >
                    <p className="text-sm line-clamp-2">{m.content}</p>
                    <div className="flex items-center gap-1.5 mt-1.5">
                      {m.source && (
                        <Badge variant="outline" className="text-[11px]">
                          {m.source.replace("connector:", "")}
                        </Badge>
                      )}
                      <span className="text-[11px] text-muted-foreground">
                        {(m.created_at || "").slice(0, 10)}
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {drawerMemory && (
          <MemoryDetailDrawer memory={drawerMemory} onClose={() => setDrawerMemory(null)} />
        )}
      </div>
    );
  }

  // ── List view ────────────────────────────────────────────────────
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">People</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Everyone you interact with, extracted automatically from calendar
          attendees, email senders/recipients, and meeting-transcript speakers.
        </p>
      </div>

      <div className="relative max-w-md">
        <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
        <Input
          className="pl-8"
          placeholder="Filter by name or email..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : filtered.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground space-y-2">
            <Users className="h-8 w-8 mx-auto opacity-40" />
            <p>No people yet.</p>
            <p>
              Import a calendar, email (.mbox), or meeting transcript in{" "}
              <a href="/settings/" className="underline">
                Settings
              </a>{" "}
              — people are extracted automatically.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {filtered.map((p) => (
            <button
              key={p.key}
              onClick={() => openPerson(p)}
              className="text-left border rounded-lg p-3 bg-card hover:bg-accent/40 transition-colors"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-sm truncate">{p.name}</span>
                <Badge variant="secondary" className="text-[11px] shrink-0">
                  {p.memory_count}
                </Badge>
              </div>
              {p.email && (
                <p className="text-xs text-muted-foreground font-mono truncate mt-0.5">{p.email}</p>
              )}
              <div className="flex items-center justify-between mt-2">
                <SourceChips sources={p.sources} />
                {p.last_seen && (
                  <span className="text-[11px] text-muted-foreground">{p.last_seen.slice(0, 10)}</span>
                )}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
