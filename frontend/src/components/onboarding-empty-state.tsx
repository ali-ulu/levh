"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
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


export function OnboardingEmptyState({ status, onChanged }: OnboardingEmptyStateProps) {
  const t = useT();
  const [loading, setLoading] = useState<"demo" | "memory" | "config" | "cleanup" | "">("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [memoryText, setMemoryText] = useState(() => t("onboarding.exampleMemory"));
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
      setError(e instanceof Error ? e.message : t("onboarding.error.actionFailed"));
    } finally {
      setLoading("");
    }
  };

  const handleLoadDemo = () =>
    run("demo", async () => {
      const result = await api.seedDemo();
      setMessage(
        result.skipped
          ? t("onboarding.demo.skipped")
          : t(
              result.seeded === 1
                ? "onboarding.demo.loaded.one"
                : "onboarding.demo.loaded.other",
              { count: result.seeded },
            )
      );
      await onChanged();
    });

  const handleStoreFirst = () =>
    run("memory", async () => {
      const content = memoryText.trim();
      if (!content) throw new Error(t("onboarding.memory.enterFirst"));
      const memory = await api.storeMemory({
        content,
        source: "onboarding",
        project: "getting-started",
        memory_type: "episodic",
      });
      const recalled = await api.recallMemories(content, 3, "getting-started", false);
      const found = recalled.memories.some((item) => item.id === memory.id);
      setMessage(
        found
          ? t("onboarding.memory.storedAndRecalled")
          : t("onboarding.memory.storedTryRecall"),
      );
      await onChanged();
    });

  const handleConfig = () =>
    run("config", async () => {
      const result = await api.onboardingMcpConfig(client, profile);
      // Codex takes TOML and Hermes YAML, so prefer the server-rendered text
      // and only fall back to JSON for an older server that omits it.
      setConfigText(result.config_text ?? JSON.stringify(result.config, null, 2));
      setMessage(
        result.config_path
          ? t("onboarding.config.generatedWithPath", {
              client: result.client,
              count: result.tool_count,
              path: result.config_path,
            })
          : t("onboarding.config.generated", {
              client: result.client,
              count: result.tool_count,
            }),
      );
    });

  const handleCopy = async () => {
    if (!configText) return;
    await navigator.clipboard.writeText(configText);
    setMessage(t("onboarding.config.copied"));
  };

  const handleCleanup = () =>
    run("cleanup", async () => {
      if (!window.confirm(t("onboarding.demo.cleanupConfirm"))) return;
      const result = await api.removeDemoData();
      setMessage(t("onboarding.demo.cleanupResult", { removed: result.removed, remaining: result.remaining }));
      await onChanged();
    });

  return (
    <Card className="border-primary/30 bg-gradient-to-b from-primary/5 to-transparent">
      <CardHeader className="pb-3">
        <CardTitle className="text-lg flex items-center gap-2">
          <Sparkles className="h-5 w-5 text-primary" />
          {status.first_run ? t("onboarding.title.first") : t("onboarding.title.finish")}
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          {t("onboarding.readinessSummary", { completed, total: status.checks.length })}
        </p>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-lg border p-4 space-y-3">
            <div className="flex items-center gap-2 font-medium">
              <Sparkles className="h-4 w-4 text-primary" /> {t("onboarding.demo.title")}
            </div>
            <p className="text-sm text-muted-foreground">
              {t("onboarding.demo.description")}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button onClick={handleLoadDemo} disabled={Boolean(loading) || status.demo_seeded}>
                {loading === "demo" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {status.demo_seeded ? t("onboarding.demo.loadedButton") : t("onboarding.demo.loadButton")}
              </Button>
              {status.demo_seeded && (
                <Button variant="outline" onClick={handleCleanup} disabled={Boolean(loading)}>
                  {loading === "cleanup" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Trash2 className="mr-2 h-4 w-4" />}
                  {t("onboarding.demo.removeButton")}
                </Button>
              )}
            </div>
          </div>

          <div className="rounded-lg border p-4 space-y-3">
            <div className="flex items-center gap-2 font-medium">
              <Database className="h-4 w-4 text-primary" /> {t("onboarding.memory.title")}
            </div>
            <p className="text-sm text-muted-foreground">
              {t("onboarding.memory.description")}
            </p>
            <Label htmlFor="first-memory">{t("onboarding.memory.label")}</Label>
            <Input id="first-memory" value={memoryText} onChange={(event) => setMemoryText(event.target.value)} />
            <Button variant="secondary" onClick={handleStoreFirst} disabled={Boolean(loading) || !memoryText.trim()}>
              {loading === "memory" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("onboarding.memory.storeAndTest")}
            </Button>
            <p className="text-xs text-muted-foreground">
              {t("onboarding.memory.meta")}
            </p>
          </div>
        </div>

        <div className="rounded-lg border p-4 space-y-3">
          <div className="flex items-center gap-2 font-medium">
            <Terminal className="h-4 w-4 text-primary" /> {t("onboarding.config.title")}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>{t("onboarding.config.client")}</Label>
              <Select value={client} onValueChange={setClient}>
                <SelectTrigger aria-label={t("onboarding.config.client")}><SelectValue /></SelectTrigger>
                <SelectContent>
                  {status.clients.map((item) => (
                    <SelectItem key={item.id} value={item.id}>{item.description}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>{t("onboarding.config.toolProfile")}</Label>
              <Select value={profile} onValueChange={setProfile}>
                <SelectTrigger aria-label={t("onboarding.config.toolProfile")}><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(status.profile_counts).map(([name, count]) => (
                    <SelectItem key={name} value={name}>{t(count === 1 ? "onboarding.config.tools.one" : "onboarding.config.tools.other", { name, count })}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" onClick={handleConfig} disabled={Boolean(loading)}>
              {loading === "config" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("onboarding.config.generate", { client, count: profileCount })}
            </Button>
            {configText && (
              <Button variant="outline" onClick={handleCopy}>
                <Clipboard className="mr-2 h-4 w-4" /> {t("onboarding.config.copy")}
              </Button>
            )}
          </div>
          {configText && <Textarea className="min-h-40 font-mono text-xs" readOnly value={configText} />}
          <p className="text-xs text-muted-foreground">{status.profile_warning}</p>
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-lg border p-4 space-y-2">
            <div className="flex items-center gap-2 font-medium">
              <ShieldCheck className="h-4 w-4 text-primary" /> {t("onboarding.dogfood.title")}
            </div>
            <p className="text-sm text-muted-foreground">{status.dogfood_statement}</p>
            <p className="text-xs">
              {t("onboarding.dogfood.metricsPrefix")}{" "}
              <strong>{status.dogfood_enabled ? t("onboarding.state.on") : t("onboarding.state.off")}</strong>
              {" · "}{t("onboarding.dogfood.journal", {
                name: status.dogfood_journal.name,
                scope: status.dogfood_journal.scope,
              })}
            </p>
            {!status.dogfood_enabled && (
              <code className="block rounded bg-muted px-2 py-1 text-xs">LEVH_DOGFOOD_ENABLED=true levh serve</code>
            )}
            <p className="text-xs text-muted-foreground">
              {t("onboarding.dogfood.restartHelp")}
            </p>
          </div>

          <div className="rounded-lg border p-4 space-y-2">
            <div className="font-medium">{t("onboarding.readiness.title")}</div>
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
          {t("onboarding.terminal.prefix")}{" "}
          <code className="rounded bg-muted px-1 py-0.5 font-mono">levh setup --demo --client claude --profile work</code>.{" "}
          {t("onboarding.terminal.see")}{" "}
          <Link href="/settings" className="underline underline-offset-4">{t("onboarding.terminal.settings")}</Link>{" "}
          {t("onboarding.terminal.suffix")}
        </p>
      </CardContent>
    </Card>
  );
}
