"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type { Finding } from "@/types";
import {
  AlertTriangle,
  Check,
  Eye,
  Inbox,
  Loader2,
  RefreshCw,
  RotateCcw,
  Trash2,
  XCircle,
} from "lucide-react";

const TABS: { status: string; label: string }[] = [
  { status: "open", label: "Open" },
  { status: "ack", label: "Acknowledged" },
  { status: "resolved", label: "Resolved" },
  { status: "ignored", label: "Ignored" },
  { status: "", label: "All" },
];

const SEVERITY_STYLE: Record<string, string> = {
  critical: "bg-red-500/15 text-red-500 border-red-500/30",
  high: "bg-orange-500/15 text-orange-500 border-orange-500/30",
  medium: "bg-yellow-500/15 text-yellow-600 border-yellow-500/30",
  low: "bg-muted text-muted-foreground",
};

function when(value: string | null) {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}

export default function FindingsPage() {
  const [items, setItems] = useState<Finding[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [tab, setTab] = useState("open");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.listFindings(tab);
      setItems(res.findings);
      setCounts(res.counts ?? {});
    } catch {
      setItems([]);
    }
    setLoading(false);
  }, [tab]);

  useEffect(() => {
    load();
  }, [load]);

  const decide = async (id: string, status: string) => {
    setBusy(id + status);
    try {
      await api.decideFinding(id, status, notes[id] ?? "");
      // The row leaves the current filter unless we are viewing everything.
      if (tab) setItems((prev) => prev.filter((i) => i.id !== id));
      else await load();
      setCounts((prev) => ({ ...prev }));
    } catch {
      /* kept on screen; the next load reconciles */
    }
    setBusy(null);
  };

  const remove = async (id: string) => {
    setBusy(id + "delete");
    try {
      await api.deleteFinding(id);
      setItems((prev) => prev.filter((i) => i.id !== id));
    } catch {
      /* kept on screen; the next load reconciles */
    }
    setBusy(null);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold">Findings</h1>
          <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
            What LEVH noticed about itself and its environment. Every entry is a
            report, never an action: nothing here changes code, runs a command,
            or leaves this machine. Repeats fold into one row — the counter says
            how often a problem was seen, not how many reports piled up.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <RefreshCw className={"h-4 w-4 mr-1.5 " + (loading ? "animate-spin" : "")} />
          Refresh
        </Button>
      </div>

      <div className="flex gap-2 flex-wrap">
        {TABS.map((t) => (
          <Button
            key={t.status || "all"}
            size="sm"
            variant={tab === t.status ? "default" : "outline"}
            onClick={() => setTab(t.status)}
          >
            {t.label}
            {counts[t.status] ? (
              <span className="ml-1.5 opacity-70">{counts[t.status]}</span>
            ) : null}
          </Button>
        ))}
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : items.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground space-y-2">
            <Inbox className="h-8 w-8 mx-auto opacity-40" />
            <p>Nothing here. An empty inbox means nothing was reported, not that nothing is wrong.</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {items.map((f) => (
            <Card key={f.id}>
              <CardContent className="p-4 space-y-3">
                <div className="flex items-start justify-between gap-3 flex-wrap">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <AlertTriangle className="h-4 w-4 text-muted-foreground shrink-0" />
                      <span className="font-medium">{f.title}</span>
                      <Badge
                        variant="outline"
                        className={SEVERITY_STYLE[f.severity] ?? SEVERITY_STYLE.low}
                      >
                        {f.severity}
                      </Badge>
                      <Badge variant="outline">{f.category}</Badge>
                      {f.occurrences > 1 ? (
                        <Badge variant="outline">seen {f.occurrences}×</Badge>
                      ) : null}
                      {!tab ? <Badge variant="outline">{f.status}</Badge> : null}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {f.source} · first {when(f.first_seen_at)} · last {when(f.last_seen_at)}
                    </div>
                  </div>
                  <code className="text-xs text-muted-foreground shrink-0">{f.id}</code>
                </div>

                {f.detail ? (
                  <pre className="text-xs bg-muted/50 rounded-md p-3 overflow-x-auto whitespace-pre-wrap">
                    {f.detail}
                  </pre>
                ) : null}

                {f.note ? (
                  <p className="text-xs text-muted-foreground">
                    <span className="font-medium">Note:</span> {f.note}
                  </p>
                ) : null}

                <div className="flex gap-2 flex-wrap items-center">
                  <Input
                    placeholder="Note (optional)"
                    className="h-8 max-w-xs"
                    value={notes[f.id] ?? ""}
                    onChange={(e) =>
                      setNotes((prev) => ({ ...prev, [f.id]: e.target.value }))
                    }
                  />
                  {f.status !== "ack" ? (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy === f.id + "ack"}
                      onClick={() => decide(f.id, "ack")}
                    >
                      <Eye className="h-4 w-4 mr-1.5" />
                      Acknowledge
                    </Button>
                  ) : null}
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy === f.id + "resolved"}
                    onClick={() => decide(f.id, "resolved")}
                  >
                    <Check className="h-4 w-4 mr-1.5" />
                    Resolved
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy === f.id + "ignored"}
                    onClick={() => decide(f.id, "ignored")}
                  >
                    <XCircle className="h-4 w-4 mr-1.5" />
                    Ignore
                  </Button>
                  {f.status !== "open" ? (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy === f.id + "open"}
                      onClick={() => decide(f.id, "open")}
                    >
                      <RotateCcw className="h-4 w-4 mr-1.5" />
                      Reopen
                    </Button>
                  ) : null}
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy === f.id + "delete"}
                    onClick={() => remove(f.id)}
                  >
                    <Trash2 className="h-4 w-4 mr-1.5" />
                    Delete
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
