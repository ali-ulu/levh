"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Loader2, Sparkles } from "lucide-react";

type AskResult = Awaited<ReturnType<typeof api.ask>>;

const EXAMPLES = [
  "Why did we choose SQLite?",
  "What did I decide about auth?",
  "What's still unresolved?",
];

export function AskPanel({ onViewSource }: { onViewSource?: (id: string) => void }) {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskResult | null>(null);
  const [error, setError] = useState("");

  const ask = async (q?: string) => {
    const query = (q ?? question).trim();
    if (!query || loading) return;
    setQuestion(query);
    setLoading(true);
    setError("");
    try {
      setResult(await api.ask(query, 6));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ask failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" />
          Ask Your Memory
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex gap-2">
          <Input
            placeholder="Ask a question about everything you've stored..."
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ask()}
          />
          <Button onClick={() => ask()} disabled={!question.trim() || loading}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Ask"}
          </Button>
        </div>

        {!result && !loading && (
          <div className="flex flex-wrap gap-1.5">
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                onClick={() => ask(ex)}
                className="text-xs px-2 py-1 rounded-full border text-muted-foreground hover:bg-accent transition-colors"
              >
                {ex}
              </button>
            ))}
          </div>
        )}

        {error && <p className="text-xs text-destructive">{error}</p>}

        {result && (
          <div className="space-y-3">
            <div className="rounded-lg bg-muted/50 p-3 text-sm leading-relaxed whitespace-pre-wrap">
              {result.answer}
            </div>
            {result.sources.length > 0 && (
              <div className="space-y-1.5">
                <p className="text-xs font-medium text-muted-foreground">
                  Grounded in {result.sources.length} memor
                  {result.sources.length === 1 ? "y" : "ies"}:
                </p>
                {result.sources.map((s) => (
                  <button
                    key={s.id}
                    onClick={() => onViewSource?.(s.id)}
                    className="w-full text-left flex items-start gap-2 text-xs rounded-md border p-2 hover:bg-accent/40 transition-colors"
                  >
                    <Badge variant="outline" className="shrink-0 text-[10px] font-mono">
                      {s.n}
                    </Badge>
                    <span className="flex-1 text-muted-foreground line-clamp-2">{s.content}</span>
                    <span className="shrink-0 text-[10px] text-muted-foreground/60">
                      {(s.created_at || "").slice(0, 10)}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
