"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { getToken, setToken, clearToken, onTokenChange } from "@/lib/token";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { BenchmarkResult, Connector, ServerConfig, SyncState, TrustBreakdown } from "@/types";
import {
  BadgeCheck,
  Check,
  Copy,
  Download,
  Gauge,
  KeyRound,
  Loader2,
  Plug,
  Server,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
  Sparkles,
  Upload,
} from "lucide-react";

// ── MCP client setup snippets ────────────────────────────────────────

const MCP_SNIPPET = (cwd: string) =>
  JSON.stringify(
    {
      mcpServers: {
        levh: {
          command: "levh",
          args: ["mcp", "stdio"],
          cwd,
          env: { EMBEDDER_MODE: "local" },
        },
      },
    },
    null,
    2
  );

const CLIENTS = [
  { id: "claude_desktop", name: "Claude Desktop", path: "~/Library/Application Support/Claude/claude_desktop_config.json" },
  { id: "claude_code", name: "Claude Code", path: "~/.claude.json" },
  { id: "cursor", name: "Cursor", path: ".cursor/mcp.json (project root)" },
  { id: "windsurf", name: "Windsurf", path: "~/.codeium/windsurf/mcp_config.json" },
  { id: "cline", name: "VS Code (Cline)", path: ".vscode/mcp.json (project root)" },
];

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="outline"
      size="sm"
      onClick={async () => {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? <Check className="h-3.5 w-3.5 mr-1.5" /> : <Copy className="h-3.5 w-3.5 mr-1.5" />}
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}

export default function SettingsPage() {
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [client, setClient] = useState("claude_desktop");
  const [installPath, setInstallPath] = useState("/path/to/levh-new");

  // Connector import
  const [selConnector, setSelConnector] = useState("local_files");
  const [connectorConfig, setConnectorConfig] = useState<Record<string, string>>({});
  const [importProject, setImportProject] = useState("");
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState("");
  const [useGate, setUseGate] = useState(true);
  const [syncState, setSyncState] = useState<SyncState[]>([]);

  // Data management
  const [exporting, setExporting] = useState(false);
  const [importingJson, setImportingJson] = useState(false);
  const [dedupeBusy, setDedupeBusy] = useState(false);
  const [dedupeResult, setDedupeResult] = useState("");
  const [consolidateSimBusy, setConsolidateSimBusy] = useState(false);
  const [consolidateSimResult, setConsolidateSimResult] = useState("");
  const [consolidateBusy, setConsolidateBusy] = useState(false);
  const [consolidateResult, setConsolidateResult] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Backup & restore
  const [backupPass, setBackupPass] = useState("");
  const [backingUp, setBackingUp] = useState(false);
  const [restorePass, setRestorePass] = useState("");
  const [restoreReplace, setRestoreReplace] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [restoreResult, setRestoreResult] = useState("");
  const backupFileRef = useRef<HTMLInputElement>(null);

  // Recall quality benchmark
  const [benchmarkBusy, setBenchmarkBusy] = useState(false);
  const [benchmarkResult, setBenchmarkResult] = useState<BenchmarkResult | null>(null);
  const [benchmarkError, setBenchmarkError] = useState("");

  // Admission gate preview
  const [admissionContent, setAdmissionContent] = useState("");
  const [admissionBusy, setAdmissionBusy] = useState(false);
  const [admissionDecision, setAdmissionDecision] = useState<{
    action: string;
    reasons: string[];
    redacted: boolean;
    secrets: string[];
    redacted_content: string;
    max_similarity: number;
  } | null>(null);
  const [admissionError, setAdmissionError] = useState("");

  // Privacy & redaction (hard-delete + redaction audit)
  const [secretsScanBusy, setSecretsScanBusy] = useState(false);
  const [secretsAudit, setSecretsAudit] = useState<{
    scanned: number;
    flagged: number;
    items: { id: string; secrets: string[]; preview: string }[];
  } | null>(null);
  const [secretsError, setSecretsError] = useState("");
  const [redactAllBusy, setRedactAllBusy] = useState(false);
  const [redactAllResult, setRedactAllResult] = useState("");

  const [trustBusy, setTrustBusy] = useState(false);
  const [trustByLabel, setTrustByLabel] = useState<Record<string, number> | null>(null);
  const [trustScored, setTrustScored] = useState<number | null>(null);
  const [trustError, setTrustError] = useState("");
  const [lowTrustBusy, setLowTrustBusy] = useState(false);
  const [lowTrust, setLowTrust] = useState<TrustBreakdown[] | null>(null);

  // Server access token (stored locally in the browser)
  const [tokenSet, setTokenSet] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  useEffect(() => {
    const sync = () => setTokenSet(getToken() !== "");
    sync();
    return onTokenChange(sync);
  }, []);
  const saveToken = () => {
    setToken(tokenInput);
    setTokenInput("");
  };

  const load = useCallback(async () => {
    try {
      const [cfg, conns] = await Promise.all([api.config(), api.listConnectors()]);
      setConfig(cfg);
      setConnectors(conns.connectors);
    } catch {}
    try {
      const s = await api.connectorSyncState();
      setSyncState(s.sync_state);
    } catch {}
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const activeConnector = connectors.find((c) => c.name === selConnector);

  const runImport = async () => {
    if (importing) return;
    setImporting(true);
    setImportResult("");
    try {
      const cfg: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(connectorConfig)) {
        if (!v.trim()) continue;
        // repos / database_ids style keys take lists
        cfg[k] = k.endsWith("s") && k !== "vault_path" ? v.split(",").map((x) => x.trim()) : v.trim();
      }
      if (useGate) {
        const r = await api.connectorSync(selConnector, cfg, importProject.trim() || undefined, true);
        setImportResult(
          `Stored ${r.stored} of ${r.fetched} items from ${r.connector} ` +
            `(${r.duplicates} duplicates skipped, ${r.redacted} secrets redacted` +
            (r.held ? `, ${r.held} held for review` : "") +
            (r.errors ? `, ${r.errors} errors` : "") +
            ").",
        );
      } else {
        const r = await api.connectorImport(selConnector, cfg, importProject.trim() || undefined);
        setImportResult(`Imported ${r.stored} of ${r.fetched} items from ${r.connector}.`);
      }
      try {
        const s = await api.connectorSyncState();
        setSyncState(s.sync_state);
      } catch {}
    } catch (e) {
      setImportResult(e instanceof Error ? e.message : "Import failed");
    }
    setImporting(false);
  };

  const exportJson = async () => {
    setExporting(true);
    try {
      const r = await api.exportMemories();
      const blob = new Blob([JSON.stringify(r.data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `levh-export-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {}
    setExporting(false);
  };

  const importJson = async (file: File) => {
    setImportingJson(true);
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      const r = await api.importMemories(Array.isArray(data) ? data : []);
      alert(`Imported ${r.imported} memories.`);
    } catch (e) {
      alert(`Import failed: ${e instanceof Error ? e.message : e}`);
    }
    setImportingJson(false);
  };

  const downloadBackup = async () => {
    setBackingUp(true);
    try {
      const { blob, filename } = await api.backup(backupPass || undefined);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert(`Backup failed: ${e instanceof Error ? e.message : e}`);
    }
    setBackingUp(false);
  };

  const restoreBackup = async (file: File) => {
    setRestoring(true);
    setRestoreResult("");
    try {
      const buf = await file.arrayBuffer();
      // base64-encode the raw bytes (handles encrypted binary blobs too)
      let binary = "";
      const bytes = new Uint8Array(buf);
      for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
      const content_b64 = btoa(binary);
      const r = await api.restore(content_b64, restorePass || undefined, restoreReplace);
      setRestoreResult(
        `Restored ${r.memories} memories and ${r.sessions} sessions (${
          r.replace ? "replaced" : "merged"
        }).`
      );
    } catch (e) {
      setRestoreResult(`Restore failed: ${e instanceof Error ? e.message : e}`);
    }
    setRestoring(false);
  };

  const runDedupe = async (apply: boolean) => {
    setDedupeBusy(true);
    setDedupeResult("");
    try {
      const r = await api.dedupe(!apply);
      setDedupeResult(
        apply
          ? `Removed ${r.removed} duplicate memories.`
          : `Found ${r.duplicates} removable duplicates. Click "Remove duplicates" to delete them.`
      );
    } catch (e) {
      setDedupeResult(e instanceof Error ? e.message : "Dedupe failed");
    }
    setDedupeBusy(false);
  };

  const runConsolidateSimilar = async (apply: boolean) => {
    setConsolidateSimBusy(true);
    setConsolidateSimResult("");
    try {
      const r = await api.consolidateSimilar(!apply);
      if (apply) {
        setConsolidateSimResult(
          `Consolidated ${r.consolidated} cluster(s), archiving ${r.archived} memories into durable summaries.`
        );
      } else {
        setConsolidateSimResult(
          r.clusters_found === 0
            ? "No consolidatable clusters (need ≥2 related, unpinned memories older than 7 days)."
            : `Found ${r.clusters_found} cluster(s) covering ${r.clusters.reduce(
                (s, c) => s + c.size,
                0
              )} memories. Click "Consolidate" to compress them.`
        );
      }
    } catch (e) {
      setConsolidateSimResult(e instanceof Error ? e.message : "Consolidation failed");
    }
    setConsolidateSimBusy(false);
  };

  const runBenchmark = async () => {
    setBenchmarkBusy(true);
    setBenchmarkError("");
    try {
      setBenchmarkResult(await api.runBenchmark(config?.embedder_mode));
    } catch (e) {
      setBenchmarkError(e instanceof Error ? e.message : "Benchmark failed");
    }
    setBenchmarkBusy(false);
  };

  const runEvaluateAdmission = async () => {
    if (admissionBusy || !admissionContent.trim()) return;
    setAdmissionBusy(true);
    setAdmissionError("");
    setAdmissionDecision(null);
    try {
      const r = await api.evaluateAdmission(admissionContent);
      setAdmissionDecision(r.decision);
    } catch (e) {
      setAdmissionError(e instanceof Error ? e.message : "Evaluation failed");
    }
    setAdmissionBusy(false);
  };

  const runAuditSecrets = async () => {
    if (secretsScanBusy) return;
    setSecretsScanBusy(true);
    setSecretsError("");
    try {
      const r = await api.auditSecrets();
      setSecretsAudit(r.audit);
    } catch (e) {
      setSecretsError(e instanceof Error ? e.message : "Scan failed");
    }
    setSecretsScanBusy(false);
  };

  const runRedactAll = async () => {
    if (redactAllBusy) return;
    if (!window.confirm("Redact secrets from all flagged memories? This rewrites their content in place.")) {
      return;
    }
    setRedactAllBusy(true);
    setRedactAllResult("");
    try {
      const r = await api.redactAll(true);
      setRedactAllResult(`Redacted ${r.redacted} of ${r.scanned} memories.`);
      await runAuditSecrets();
    } catch (e) {
      setRedactAllResult(e instanceof Error ? e.message : "Redaction failed");
    }
    setRedactAllBusy(false);
  };

  const runRecomputeTrust = async () => {
    if (trustBusy) return;
    setTrustBusy(true);
    setTrustError("");
    try {
      const r = await api.recomputeTrust();
      setTrustScored(r.scored);
      setTrustByLabel(r.by_label);
      await runLowTrust();
    } catch (e) {
      setTrustError(e instanceof Error ? e.message : "Recompute failed");
    }
    setTrustBusy(false);
  };

  const runLowTrust = async () => {
    if (lowTrustBusy) return;
    setLowTrustBusy(true);
    try {
      const r = await api.lowTrust(0.4, 10);
      setLowTrust(r.low_trust);
    } catch (e) {
      setTrustError(e instanceof Error ? e.message : "Low-trust lookup failed");
    }
    setLowTrustBusy(false);
  };

  const runConsolidate = async () => {
    setConsolidateBusy(true);
    setConsolidateResult("");
    try {
      const r = await api.consolidate();
      setConsolidateResult(`Promoted ${r.consolidated} short-term memories to episodic.`);
    } catch (e) {
      setConsolidateResult(e instanceof Error ? e.message : "Consolidation failed");
    }
    setConsolidateBusy(false);
  };

  const selectedClient = CLIENTS.find((c) => c.id === client)!;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Server configuration, AI client setup, imports, and data management.
        </p>
      </div>

      {/* Server config */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Server className="h-4 w-4" />
            Server Configuration
          </CardTitle>
        </CardHeader>
        <CardContent>
          {!config ? (
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-3 text-sm">
              <div>
                <div className="text-xs text-muted-foreground">Embedder</div>
                <div className="flex items-center gap-2">
                  {config.embedder_mode}
                  {config.embedder_dimension && (
                    <Badge variant="outline" className="text-[11px]">{config.embedder_dimension}-d</Badge>
                  )}
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Database</div>
                <div className="font-mono text-xs truncate">{config.db_path}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Short-term capacity</div>
                <div>{config.short_term_max}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Default half-life</div>
                <div>{config.decay_half_life_hours} h</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">H(x,ψ) weights</div>
                <div className="font-mono text-xs">
                  α={config.weights.alpha} β={config.weights.beta} γ={config.weights.gamma} δ={config.weights.delta}
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Reinforcement gain</div>
                <div>+{(config.reinforcement_gain * 100).toFixed(0)}%–{(config.reinforcement_gain * 150).toFixed(0)}% per recall</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Max stability</div>
                <div>{(config.max_stability_hours / 24).toFixed(0)} days</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Auto-summarize sessions</div>
                <Badge variant={config.auto_summarize_sessions ? "default" : "secondary"} className="text-[11px]">
                  {config.auto_summarize_sessions ? "on" : "off"}
                </Badge>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Version</div>
                <div>{config.version}</div>
              </div>
            </div>
          )}
          <p className="text-xs text-muted-foreground mt-3">
            Every memory has its own half-life that starts at the default and grows every time
            it&apos;s recalled or reinforced — like spaced repetition. Configure via environment
            variables (<code className="bg-muted px-1 rounded">.env</code>): EMBEDDER_MODE,
            SQLITE_DB_PATH, SHORT_TERM_MAX, DECAY_HALF_LIFE_HOURS, HSCORE_ALPHA…DELTA,
            REINFORCEMENT_GAIN, MAX_STABILITY_HOURS, AUTO_SUMMARIZE_SESSIONS.
          </p>
        </CardContent>
      </Card>

      {/* Server access token */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <KeyRound className="h-4 w-4" />
            Server access token
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-2 text-sm">
            <span className="text-xs text-muted-foreground">Status</span>
            <Badge variant={tokenSet ? "default" : "secondary"} className="text-[11px]">
              {tokenSet ? "•••• set" : "none"}
            </Badge>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">Access token</Label>
              <Input
                type="password"
                className="w-64"
                placeholder="Paste the server token"
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
              />
            </div>
            <Button variant="outline" onClick={saveToken} disabled={!tokenInput.trim()}>
              Save token
            </Button>
            <Button variant="outline" onClick={() => clearToken()} disabled={!tokenSet}>
              Clear token
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
              Only needed when this server was started with LEVH_TOKEN. Legacy STACKMEMORY_TOKEN
              is still accepted. Stored locally in
            your browser.
          </p>
        </CardContent>
      </Card>

      {/* Recall quality benchmark */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Gauge className="h-4 w-4" />
            Recall Quality
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-muted-foreground">
            Runs a labelled query set against an isolated temp database (never touches your
            real memories) and reports hit@k / MRR — the same kind of accuracy metric managed
            memory services publish. Uses your configured embedder (
            <strong className="text-foreground">{config?.embedder_mode ?? "…"}</strong>
            {config?.embedder_mode === "hash" && " — non-semantic, treat as a plumbing check only"}
            ).
          </p>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={runBenchmark} disabled={benchmarkBusy}>
              {benchmarkBusy && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Run benchmark
            </Button>
            {benchmarkError && <span className="text-xs text-destructive">{benchmarkError}</span>}
          </div>
          {benchmarkResult && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-1">
              {(
                [
                  ["hit@1", benchmarkResult["hit@1"]],
                  ["hit@3", benchmarkResult["hit@3"]],
                  ["hit@5", benchmarkResult["hit@5"]],
                  ["MRR", benchmarkResult.mrr],
                ] as const
              ).map(([label, value]) => (
                <div key={label} className="rounded-lg border p-2.5 text-center">
                  <div className="text-lg font-bold tabular-nums">{(value * 100).toFixed(0)}%</div>
                  <div className="text-[11px] text-muted-foreground">{label}</div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Admission gate preview */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <ShieldQuestion className="h-4 w-4" />
            Admission Gate (preview)
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-muted-foreground">
            Preview how a candidate memory would be judged before storing: admitted, held for
            review (near-duplicate), redacted (secrets stripped), or rejected (too short / an
            exact duplicate). This is read-only — it does not store anything.
          </p>
          <Textarea
            placeholder="Paste candidate memory text to evaluate…"
            value={admissionContent}
            onChange={(e) => setAdmissionContent(e.target.value)}
            rows={3}
          />
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={runEvaluateAdmission}
              disabled={admissionBusy || !admissionContent.trim()}
            >
              {admissionBusy && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Evaluate
            </Button>
            {admissionError && (
              <span className="text-xs text-destructive">{admissionError}</span>
            )}
          </div>
          {admissionDecision && (
            <div className="space-y-2 pt-2 border-t">
              <div className="flex items-center gap-2">
                <Badge
                  variant={
                    admissionDecision.action === "admit"
                      ? "default"
                      : admissionDecision.action === "redact"
                      ? "secondary"
                      : admissionDecision.action === "review"
                      ? "outline"
                      : "destructive"
                  }
                >
                  {admissionDecision.action.toUpperCase()}
                </Badge>
                {admissionDecision.reasons.length > 0 && (
                  <span className="text-xs text-muted-foreground">
                    {admissionDecision.reasons.join(", ")}
                  </span>
                )}
              </div>
              {admissionDecision.redacted && (
                <div className="text-xs">
                  <span className="text-muted-foreground">Redacted preview: </span>
                  <span className="font-mono bg-muted px-1 rounded">
                    {admissionDecision.redacted_content}
                  </span>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Privacy & Redaction */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <ShieldAlert className="h-4 w-4" />
            Privacy &amp; Redaction
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-muted-foreground">
            Scan stored memories for secrets (credentials, tokens, API keys) that slipped in
            before the admission gate existed, and strip them in place.
          </p>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={runAuditSecrets} disabled={secretsScanBusy}>
              {secretsScanBusy && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Scan for secrets
            </Button>
            <Button
              variant="outline"
              onClick={runRedactAll}
              disabled={redactAllBusy || !secretsAudit || secretsAudit.flagged === 0}
            >
              {redactAllBusy && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Redact all
            </Button>
            {secretsError && <span className="text-xs text-destructive">{secretsError}</span>}
          </div>
          {redactAllResult && (
            <p className="text-xs text-muted-foreground">{redactAllResult}</p>
          )}
          {secretsAudit && (
            <div className="space-y-2 pt-2 border-t">
              <p className="text-xs text-muted-foreground">
                Scanned {secretsAudit.scanned} memories —{" "}
                <span className={secretsAudit.flagged > 0 ? "text-amber-600 dark:text-amber-500" : ""}>
                  {secretsAudit.flagged} flagged
                </span>
                .
              </p>
              {secretsAudit.items.length > 0 && (
                <ul className="space-y-1">
                  {secretsAudit.items.slice(0, 10).map((item) => (
                    <li key={item.id} className="text-xs text-muted-foreground">
                      <span className="font-mono">{item.id.slice(0, 8)}</span>{" "}
                      <Badge variant="secondary" className="text-[11px]">
                        {item.secrets.join(", ")}
                      </Badge>{" "}
                      {item.preview}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Trust & provenance */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <BadgeCheck className="h-4 w-4" />
            Trust &amp; provenance
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-muted-foreground">
            A deterministic, explainable provenance signal — where a memory came from, how many
            independent sources corroborate it, and whether it carries risk flags. This is not a
            truth score, and it never changes recall ranking.
          </p>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={runRecomputeTrust} disabled={trustBusy}>
              {trustBusy && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Recompute trust scores
            </Button>
            {trustError && <span className="text-xs text-destructive">{trustError}</span>}
          </div>
          {trustScored !== null && trustByLabel && (
            <div className="space-y-2 pt-2 border-t">
              <p className="text-xs text-muted-foreground">Scored {trustScored} memories.</p>
              <div className="flex flex-wrap gap-2">
                {Object.entries(trustByLabel).map(([label, count]) => (
                  <Badge key={label} variant="secondary" className="text-[11px]">
                    {label}: {count}
                  </Badge>
                ))}
              </div>
            </div>
          )}
          {lowTrust && lowTrust.length > 0 && (
            <div className="space-y-2 pt-2 border-t">
              <p className="text-xs text-muted-foreground">Lowest-trust memories</p>
              <ul className="space-y-1">
                {lowTrust.map((t) => (
                  <li key={t.memory_id} className="text-xs text-muted-foreground">
                    <Badge variant="secondary" className="text-[11px]">
                      {t.label}
                    </Badge>{" "}
                    <span className="font-mono">{t.confidence.toFixed(2)}</span>{" "}
                    <span className="font-mono">{t.memory_id.slice(0, 8)}</span> —{" "}
                    {t.evidence?.source ?? "unknown"} source
                  </li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>

      {/* MCP client setup */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Sparkles className="h-4 w-4" />
            Connect an AI Client
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2 items-end">
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">Client</Label>
              <Select value={client} onValueChange={setClient}>
                <SelectTrigger className="w-48">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CLIENTS.map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      {c.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1 flex-1 min-w-56">
              <Label className="text-xs text-muted-foreground">LEVH install path</Label>
              <Input value={installPath} onChange={(e) => setInstallPath(e.target.value)} />
            </div>
            <CopyButton text={MCP_SNIPPET(installPath)} />
          </div>
          <p className="text-xs text-muted-foreground">
            Add this to <code className="bg-muted px-1 rounded">{selectedClient.path}</code>:
          </p>
          <pre className="text-xs bg-muted rounded-lg p-3 overflow-auto">{MCP_SNIPPET(installPath)}</pre>
          <p className="text-xs text-muted-foreground">
            Or generate configs from the CLI:{" "}
            <code className="bg-muted px-1 rounded">levh mcp config {client}</code>
          </p>
        </CardContent>
      </Card>

      {/* Connectors */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Plug className="h-4 w-4" />
            Connectors
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            Bring in real data (calendar, email, Notion, GitHub, transcripts). Attendees and
            senders/recipients feed the knowledge graph on the dashboard and Graph page — without
            a connector or manually-set metadata, plain-text memories won&apos;t produce people or
            organization nodes.
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {connectors.map((c) => (
              <Button
                key={c.name}
                variant={selConnector === c.name ? "default" : "outline"}
                size="sm"
                onClick={() => {
                  setSelConnector(c.name);
                  setConnectorConfig({});
                  setImportResult("");
                }}
              >
                {c.name}
              </Button>
            ))}
          </div>
          {activeConnector && (
            <>
              <p className="text-xs text-muted-foreground">{activeConnector.description}</p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {activeConnector.required_config_keys.map((key) => (
                  <div key={key} className="space-y-1">
                    <Label className="text-xs">{key}</Label>
                    <Input
                      placeholder={
                        key === "vault_path" || key === "directory"
                          ? "/absolute/path"
                          : key === "ics_path"
                          ? "/path/to/calendar.ics (exported from Google/Outlook)"
                          : key === "mbox_path"
                          ? "/path/to/mail.mbox (Gmail Takeout, Thunderbird…)"
                          : key === "transcript_path"
                          ? "/path/to/meeting.vtt (Zoom/Meet/Teams/Otter)"
                          : key === "repos"
                          ? "owner/repo, owner/other"
                          : key
                      }
                      value={connectorConfig[key] ?? ""}
                      onChange={(e) =>
                        setConnectorConfig((prev) => ({ ...prev, [key]: e.target.value }))
                      }
                    />
                  </div>
                ))}
                <div className="space-y-1">
                  <Label className="text-xs">Store under project (optional)</Label>
                  <Input
                    value={importProject}
                    onChange={(e) => setImportProject(e.target.value)}
                    placeholder="my-repo"
                  />
                </div>
              </div>
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
                <input
                  type="checkbox"
                  checked={useGate}
                  onChange={(e) => setUseGate(e.target.checked)}
                />
                Route through admission gate (dedupe + redact secrets)
              </label>
              <div className="flex items-center gap-3">
                <Button onClick={runImport} disabled={importing}>
                  {importing && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                  Run import
                </Button>
                {importResult && <span className="text-xs text-muted-foreground">{importResult}</span>}
              </div>
            </>
          )}
          {syncState.length > 0 && (
            <div className="pt-2 border-t space-y-1">
              <Label className="text-xs text-muted-foreground">Sync history</Label>
              <ul className="space-y-1">
                {syncState.map((s) => (
                  <li key={s.source_key} className="text-xs text-muted-foreground">
                    {s.connector}
                    {s.project ? ` [${s.project}]` : ""} — last synced{" "}
                    {new Date(s.last_synced_at).toLocaleString()}, {s.total_stored} stored
                  </li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Data management */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Download className="h-4 w-4" />
            Data Management
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={exportJson} disabled={exporting}>
              {exporting ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Download className="h-4 w-4 mr-2" />
              )}
              Export all memories (JSON)
            </Button>
            <Button
              variant="outline"
              onClick={() => fileInputRef.current?.click()}
              disabled={importingJson}
            >
              {importingJson ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Upload className="h-4 w-4 mr-2" />
              )}
              Import from JSON
            </Button>
            <input
              ref={fileInputRef}
              type="file"
              accept="application/json"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) importJson(f);
                e.target.value = "";
              }}
            />
          </div>

          <div className="flex flex-wrap items-center gap-2 pt-2 border-t">
            <Button variant="outline" onClick={runConsolidate} disabled={consolidateBusy}>
              {consolidateBusy && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Consolidate short-term
            </Button>
            {consolidateResult && (
              <span className="text-xs text-muted-foreground">{consolidateResult}</span>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2 pt-2 border-t">
            <Button variant="outline" onClick={() => runDedupe(false)} disabled={dedupeBusy}>
              {dedupeBusy && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Find duplicates
            </Button>
            <Button variant="outline" onClick={() => runDedupe(true)} disabled={dedupeBusy}>
              Remove duplicates
            </Button>
            {dedupeResult && <span className="text-xs text-muted-foreground">{dedupeResult}</span>}
          </div>

          <div className="flex flex-wrap items-center gap-2 pt-2 border-t">
            <Button
              variant="outline"
              onClick={() => runConsolidateSimilar(false)}
              disabled={consolidateSimBusy}
            >
              {consolidateSimBusy && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Preview consolidation
            </Button>
            <Button
              variant="outline"
              onClick={() => runConsolidateSimilar(true)}
              disabled={consolidateSimBusy}
            >
              Consolidate
            </Button>
            {consolidateSimResult && (
              <span className="text-xs text-muted-foreground">{consolidateSimResult}</span>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            Consolidation compresses clusters of related, aged (&gt;7d), unpinned memories
            into one durable summary each — like sleep consolidating episodes into a gist.
            Originals are archived inside the summary, not lost.
          </p>
        </CardContent>
      </Card>

      {/* Backup & Restore */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <ShieldCheck className="h-4 w-4" />
            Backup &amp; Restore
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-xs text-muted-foreground">
            A full snapshot of every memory (with its decay state) and session.
            Set a passphrase to encrypt the file at rest — it&apos;s the only way
            to read it back, so store it safely; it cannot be recovered.
          </p>

          <div className="flex flex-wrap items-end gap-2">
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">
                Passphrase (optional — encrypts the backup)
              </Label>
              <Input
                type="password"
                className="w-64"
                placeholder="Leave blank for plain JSON"
                value={backupPass}
                onChange={(e) => setBackupPass(e.target.value)}
              />
            </div>
            <Button variant="outline" onClick={downloadBackup} disabled={backingUp}>
              {backingUp ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Download className="h-4 w-4 mr-2" />
              )}
              Download backup
            </Button>
          </div>

          <div className="space-y-2 pt-3 border-t">
            <Label className="text-xs text-muted-foreground">Restore from a backup file</Label>
            <div className="flex flex-wrap items-end gap-2">
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">
                  Passphrase (if the file is encrypted)
                </Label>
                <Input
                  type="password"
                  className="w-64"
                  placeholder="Only for encrypted backups"
                  value={restorePass}
                  onChange={(e) => setRestorePass(e.target.value)}
                />
              </div>
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground pb-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={restoreReplace}
                  onChange={(e) => setRestoreReplace(e.target.checked)}
                />
                Replace (wipe current data first)
              </label>
              <Button
                variant="outline"
                onClick={() => backupFileRef.current?.click()}
                disabled={restoring}
              >
                {restoring ? (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                ) : (
                  <Upload className="h-4 w-4 mr-2" />
                )}
                Choose file &amp; restore
              </Button>
              <input
                ref={backupFileRef}
                type="file"
                accept=".json,.smbackup,application/octet-stream,application/json"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) restoreBackup(f);
                  e.target.value = "";
                }}
              />
            </div>
            {restoreReplace && (
              <p className="text-xs text-amber-600 dark:text-amber-500">
                Replace mode deletes all current memories and sessions before restoring.
              </p>
            )}
            {restoreResult && (
              <p className="text-xs text-muted-foreground">{restoreResult}</p>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
