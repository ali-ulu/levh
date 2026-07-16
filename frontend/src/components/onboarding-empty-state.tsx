"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import type { OnboardingStatus } from "@/types";
import {
  CheckCircle2,
  Clipboard,
  Database,
  Loader2,
  ShieldCheck,
  Sparkles,
  Terminal,
  Trash2,
} from "lucide-react";

interface OnboardingEmptyStateProps {
  status: OnboardingStatus;
  onChanged: () => void | Promise<void>;
}

const EXAMPLE_MEMORY = "Atlas project uses PostgreSQL in production.";

export function OnboardingEmptyState({ status, onChanged }: OnboardingEmptyStateProps) {
  const [loading, setLoading] = useState<"demo" | "memory" | "config" | "cleanup" | "">("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [memoryText, setMemoryText] = useState(EXAMPLE_MEMORY);
  const [client, setClient] = useState(status.mcp_client || "claude");
  const [profile, setProfile] = useState(status.mcp_profile || status.mcp_default_profile || "work");
  const [configText, setConfigText] = useState("");

  useEffect(() => {
    if (status.mcp_client) setClient(status.mcp_client);
    if (status.mcp_profile) setProfile(status.mcp_profile);
  }, [status.mcp_client, status.mcp_profile]);

  const profileCount = status.profile_counts[profile] ?? 0;
  const completed = useMemo(
    () => status.checks.filter((check) => check.status === "pass").length,
    [status.checks]
  );

  const run = async (kind: typeof loading, fn: () => Promise<void>) => {
    if (loading) return;
    setLoading(kind);
    setError("");
    setMessage("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
    } finally {
      setLoading("");
    }
  };

  const handleLoadDemo = () =>
    run("demo", async () => {
      const result = await api.seedDemo();
      setMessage(
        result.skipped
          ? "The store already contains data; nothing was overwritten."
          : `Loaded ${result.seeded} deterministic demo memories.`
      );
      await onChanged();
    });

  const handleStoreFirst = () =>
    run("memory", async () => {
      const content = memoryText.trim();
      if (!content) throw new Error("Enter a memory first");
      const memory = await api.storeMemory({
        content,
        source: "onboarding",
        project: "getting-started",
        memory_type: "episodic",
      });
      const recalled = await api.recallMemories(content, 3, "getting-started", false);
      const found = recalled.memories.some((item) => item.id === memory.id);
      setMessage(found ? "First memory stored and recalled through the real pipeline." : "Memory stored; try recall from the dashboard.");
      await onChanged();
    });

  const handleConfig = () =>
    run("config", async () => {
      const result = await api.onboardingMcpConfig(client, profile);
      setConfigText(JSON.stringify(result.config, null, 2));
      setMessage(`${result.client} config generated with ${result.tool_count} advertised tools.`);
    });

  const handleCopy = async () => {
    if (!configText) return;
    await navigator.clipboard.writeText(configText);
    setMessage("MCP config copied.");
  };

  const handleCleanup = () =>
    run("cleanup", async () => {
      if (!window.confirm("Remove only demo-tagged memories? Real memories will be preserved.")) return;
      const result = await api.removeDemoData();
      setMessage(`Removed ${result.removed} demo memories; ${result.remaining} memories remain.`);
      await onChanged();
    });

  return (
    <Card className="border-primary/30 bg-gradient-to-b from-primary/5 to-transparent">
      <CardHeader className="pb-3">
        <CardTitle className="text-lg flex items-center gap-2">
          <Sparkles className="h-5 w-5 text-primary" />
          {status.first_run ? "Set up LEVH" : "Finish your LEVH setup"}
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          {completed}/{status.checks.length} readiness checks complete. Choose demo data or start with a real memory; neither path overwrites an existing store.
        </p>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-lg border p-4 space-y-3">
            <div className="flex items-center gap-2 font-medium">
              <Sparkles className="h-4 w-4 text-primary" /> Try LEVH
            </div>
            <p className="text-sm text-muted-foreground">
              Load the deterministic 20-memory corpus with people, organizations, trust scores, decay, and one reviewable conflict candidate.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button onClick={handleLoadDemo} disabled={Boolean(loading) || status.demo_seeded}>
                {loading === "demo" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {status.demo_seeded ? "Demo data loaded" : "Load demo data"}
              </Button>
              {status.demo_seeded && (
                <Button variant="outline" onClick={handleCleanup} disabled={Boolean(loading)}>
                  {loading === "cleanup" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Trash2 className="mr-2 h-4 w-4" />}
                  Remove demo data
                </Button>
              )}
            </div>
          </div>

          <div className="rounded-lg border p-4 space-y-3">
            <div className="flex items-center gap-2 font-medium">
              <Database className="h-4 w-4 text-primary" /> Set up real memory
            </div>
            <p className="text-sm text-muted-foreground">
              Store one ordinary memory through the existing pipeline, then verify it can be recalled.
            </p>
            <Label htmlFor="first-memory">First memory</Label>
            <Input id="first-memory" value={memoryText} onChange={(event) => setMemoryText(event.target.value)} />
            <Button variant="secondary" onClick={handleStoreFirst} disabled={Boolean(loading) || !memoryText.trim()}>
              {loading === "memory" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Store and test recall
            </Button>
            <p className="text-xs text-muted-foreground">
              Source: onboarding · Project: getting-started · No silent pinning or trust boost.
            </p>
          </div>
        </div>

        <div className="rounded-lg border p-4 space-y-3">
          <div className="flex items-center gap-2 font-medium">
            <Terminal className="h-4 w-4 text-primary" /> Connect an AI client
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>Client</Label>
              <Select value={client} onValueChange={setClient}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {status.clients.map((item) => (
                    <SelectItem key={item.id} value={item.id}>{item.description}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Tool profile</Label>
              <Select value={profile} onValueChange={setProfile}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(status.profile_counts).map(([name, count]) => (
                    <SelectItem key={name} value={name}>{name} · {count} tools</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" onClick={handleConfig} disabled={Boolean(loading)}>
              {loading === "config" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Generate {client} config ({profileCount} tools)
            </Button>
            {configText && (
              <Button variant="outline" onClick={handleCopy}>
                <Clipboard className="mr-2 h-4 w-4" /> Copy config
              </Button>
            )}
          </div>
          {configText && <Textarea className="min-h-40 font-mono text-xs" readOnly value={configText} />}
          <p className="text-xs text-muted-foreground">{status.profile_warning}</p>
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-lg border p-4 space-y-2">
            <div className="flex items-center gap-2 font-medium">
              <ShieldCheck className="h-4 w-4 text-primary" /> Local usage measurement
            </div>
            <p className="text-sm text-muted-foreground">{status.dogfood_statement}</p>
            <p className="text-xs">
              Dogfood metrics: <strong>{status.dogfood_enabled ? "On" : "Off"}</strong> · Journal: {status.dogfood_journal.name} ({status.dogfood_journal.scope})
            </p>
            {!status.dogfood_enabled && (
              <code className="block rounded bg-muted px-2 py-1 text-xs">STACKMEMORY_DOGFOOD_ENABLED=true stackmemory serve</code>
            )}
            <p className="text-xs text-muted-foreground">
              A process started without the flag must be restarted. Historical journals are not migrated automatically.
            </p>
          </div>

          <div className="rounded-lg border p-4 space-y-2">
            <div className="font-medium">Readiness</div>
            {status.checks.map((check) => (
              <div key={check.id} className="flex items-start gap-2 text-sm">
                <CheckCircle2 className={`mt-0.5 h-4 w-4 ${check.status === "pass" ? "text-primary" : "text-muted-foreground"}`} />
                <span><strong className="capitalize">{check.id}:</strong> {check.message}</span>
              </div>
            ))}
          </div>
        </div>

        {message && <p className="text-sm text-primary">{message}</p>}
        {error && <p className="text-sm text-destructive">{error}</p>}

        <p className="text-xs text-muted-foreground">
          Terminal path: <code className="rounded bg-muted px-1 py-0.5 font-mono">stackmemory setup --demo --client claude --profile work</code>. See{" "}
          <Link href="/settings" className="underline underline-offset-4">Settings</Link> for the full local configuration surface.
        </p>
      </CardContent>
    </Card>
  );
}
