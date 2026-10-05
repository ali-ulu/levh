"use client";

import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { Connector, SyncJob, SyncState } from "@/types";
import {
  Calendar,
  Check,
  FileText,
  FolderGit2,
  GitBranch,
  Loader2,
  Mail,
  MessageSquare,
  Music,
  NotepadText,
  Plug,
  Upload,
} from "lucide-react";

// Connector config keys that name a single file the server has to read.
const FILE_CONFIG_KEYS: Record<string, string> = {
  ics_path: ".ics",
  mbox_path: ".mbox,.eml",
  transcript_path: ".vtt,.srt,.txt",
};

// Visual metadata for connectors
const CONNECTOR_META: Record<
  string,
  { icon: typeof Plug; color: string; description: string; category: string }
> = {
  local_files: {
    icon: FolderGit2,
    color: "text-blue-500",
    description: "settings.connectors.meta.localFiles.description",
    category: "settings.connectors.category.files",
  },
  calendar: {
    icon: Calendar,
    color: "text-emerald-500",
    description: "settings.connectors.meta.calendar.description",
    category: "settings.connectors.category.productivity",
  },
  email: {
    icon: Mail,
    color: "text-purple-500",
    description: "settings.connectors.meta.email.description",
    category: "settings.connectors.category.productivity",
  },
  transcripts: {
    icon: Music,
    color: "text-amber-500",
    description: "settings.connectors.meta.transcripts.description",
    category: "settings.connectors.category.productivity",
  },
  obsidian: {
    icon: NotepadText,
    color: "text-violet-500",
    description: "settings.connectors.meta.obsidian.description",
    category: "settings.connectors.category.notes",
  },
  notion: {
    icon: FileText,
    color: "text-gray-500",
    description: "settings.connectors.meta.notion.description",
    category: "settings.connectors.category.notes",
  },
  github: {
    icon: GitBranch,
    color: "text-gray-700 dark:text-gray-300",
    description: "settings.connectors.meta.github.description",
    category: "settings.connectors.category.development",
  },
};

CONNECTOR_META.slack = {
  icon: MessageSquare,
  color: "text-sky-500",
  description: "settings.connectors.slack.description",
  category: CONNECTOR_META.calendar.category,
};

export function Connectors() {
  const t = useT();
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [selConnector, setSelConnector] = useState("local_files");
  const [connectorConfig, setConnectorConfig] = useState<Record<string, string>>({});
  const [importProject, setImportProject] = useState("");
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState("");
  const [useGate, setUseGate] = useState(true);
  const [background, setBackground] = useState(true);
  const [gitHistory, setGitHistory] = useState(true);
  const [gitBlame, setGitBlame] = useState(false);
  const [gitSnapshot, setGitSnapshot] = useState(true);
  const [syncState, setSyncState] = useState<SyncState[]>([]);
  const [jobs, setJobs] = useState<SyncJob[]>([]);
  const [uploadingKey, setUploadingKey] = useState("");
  const mounted = useRef(true);

  const [serverError, setServerError] = useState(false);

  useEffect(() => {
    mounted.current = true;
    api
      .listConnectors()
      .then((r) => setConnectors(r.connectors))
      .catch(() => {
        setConnectors([]);
        setServerError(true);
      });
    api
      .connectorSyncState()
      .then((r) => setSyncState(r.sync_state))
      .catch(() => setSyncState([]));
    api
      .listSyncJobs()
      .then(setJobs)
      .catch(() => setJobs([]));
    return () => {
      mounted.current = false;
    };
  }, []);

  const activeConnector = connectors.find((c) => c.name === selConnector);
  const activeMeta = CONNECTOR_META[selConnector];

  const formatDuration = (timing?: Record<string, number>) => {
    if (!timing) return "";
    const ms =
      timing.job_total ?? timing.route_total ?? timing.total ?? timing.items;
    if (ms === undefined) return "";
    return ms >= 1000
      ? t("settings.connectors.duration.seconds", { value: (ms / 1000).toFixed(1) })
      : t("settings.connectors.duration.milliseconds", { value: Math.round(ms) });
  };

  const refreshSyncState = async () => {
    try {
      const s = await api.connectorSyncState();
      if (mounted.current) setSyncState(s.sync_state);
    } catch {}
    try {
      const j = await api.listSyncJobs();
      if (mounted.current) setJobs(j);
    } catch {}
  };

  const runImport = async () => {
    if (importing) return;
    setImporting(true);
    setImportResult("");
    try {
      const cfg: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(connectorConfig)) {
        if (!v.trim()) continue;
        cfg[k] =
          k.endsWith("s") && k !== "vault_path"
            ? v.split(",").map((x) => x.trim())
            : v.trim();
      }
      if (selConnector === "git") {
        cfg.include_file_history = gitHistory;
        cfg.include_blame = gitBlame;
        cfg.include_snapshot = gitSnapshot;
      }
      if (background) {
        const accepted = await api.connectorSyncBackground(
          selConnector,
          cfg,
          importProject.trim() || undefined,
          true
        );
        // Older servers ignore the unknown `background` flag and answer 200
        // with the full report (pydantic drops unknown fields). Handle both.
        const legacy = accepted as unknown as {
          stored?: number;
          fetched?: number;
          timing_ms?: Record<string, number>;
        };
        if (!accepted.job_id && legacy.stored !== undefined) {
          setImportResult(
            t("settings.connectors.result.stored", {
              stored: legacy.stored ?? 0,
              fetched: legacy.fetched ?? 0,
              connector: selConnector,
              duration: formatDuration(legacy.timing_ms),
            })
          );
          await refreshSyncState();
        } else {
        // The server answers 202 at once; the pipeline keeps running.
        // Poll the job instead of the socket — a slow sync must never hang
        // this button the way the synchronous call did.
        let job: SyncJob | null = null;
        let failures = 0;
        for (let i = 0; i < 150; i++) {
          await new Promise((r) => setTimeout(r, 2000));
          if (!mounted.current) return;
          try {
            job = await api.getSyncJob(accepted.job_id);
            failures = 0;
          } catch (e) {
            if (++failures >= 5) throw e;
            continue;
          }
          if (job.status === "done" || job.status === "error") break;
          setImportResult(
            t("settings.connectors.result.syncing", {
              connector: selConnector,
              status: job.status,
              job: accepted.job_id.slice(0, 8),
            })
          );
        }
        if (!job || (job.status !== "done" && job.status !== "error")) {
          setImportResult(
            t("settings.connectors.result.jobStillRunning", {
              job: accepted.job_id.slice(0, 8),
              status: job?.status ?? "running",
            })
          );
        } else if (job.status === "error") {
          setImportResult(t("settings.connectors.result.backgroundFailed", { error: job.error ?? t("settings.connectors.background.unknownError") }));
        } else {
          const r = job.result!;
          setImportResult(
            t("settings.connectors.result.gatedStored", {
              stored: r.stored,
              fetched: r.fetched,
              connector: selConnector,
              duplicates: r.duplicates,
              redacted: r.redacted,
              held: r.held ? t("settings.connectors.result.held", { count: r.held }) : "",
              errors: r.errors ? t("settings.connectors.result.errors", { count: r.errors }) : "",
              duration: formatDuration(r.timing_ms),
            })
          );
        }
        await refreshSyncState();
        }
      } else if (useGate) {
        const r = await api.connectorSync(
          selConnector,
          cfg,
          importProject.trim() || undefined,
          true
        );
        setImportResult(
          t("settings.connectors.result.gatedStored", {
            stored: r.stored,
            fetched: r.fetched,
            connector: r.connector,
            duplicates: r.duplicates,
            redacted: r.redacted,
            held: r.held ? t("settings.connectors.result.held", { count: r.held }) : "",
            errors: r.errors ? t("settings.connectors.result.errors", { count: r.errors }) : "",
            duration: formatDuration(r.timing_ms),
          })
        );
      } else {
        const r = await api.connectorImport(
          selConnector,
          cfg,
          importProject.trim() || undefined
        );
        setImportResult(
          t("settings.connectors.result.imported", {
            stored: r.stored,
            fetched: r.fetched,
            connector: r.connector,
          })
        );
      }
      await refreshSyncState();
    } catch (e) {
      setImportResult(e instanceof Error ? e.message : t("settings.connectors.result.importFailed"));
    }
    setImporting(false);
  };

  const uploadConnectorFile = async (key: string, file: File) => {
    setUploadingKey(key);
    setImportResult("");
    try {
      const buffer = await file.arrayBuffer();
      const bytes = new Uint8Array(buffer);
      const parts: string[] = [];
      for (let i = 0; i < bytes.length; i += 0x8000) {
        parts.push(
          String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + 0x8000)))
        );
      }
      const binary = parts.join("");
      const r = await api.connectorUpload(file.name, btoa(binary));
      setConnectorConfig((prev) => ({ ...prev, [key]: r.path }));
      setImportResult(
        t("settings.connectors.result.uploaded", {
          filename: r.filename,
          kb: (r.bytes / 1024).toFixed(0),
        })
      );
    } catch (e) {
      setImportResult(e instanceof Error ? e.message : t("settings.connectors.result.uploadFailed"));
    }
    setUploadingKey("");
  };

  // Group connectors by category
  const categories = Array.from(new Set(connectors.map((c) => CONNECTOR_META[c.name]?.category || "settings.connectors.category.other")));

  const lastSyncFor = (name: string) =>
    syncState.find((s) => s.connector === name);

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Plug className="h-4 w-4" />
          {t("settings.connectors.title")}
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          {t("settings.connectors.description")}
        </p>
      </CardHeader>
      <CardContent className="space-y-5">
        {/* Server offline notice */}
        {serverError && connectors.length === 0 && (
          <div className="rounded-xl border border-dashed border-amber-500/30 bg-amber-500/5 p-4 text-center">
            <Plug className="h-8 w-8 mx-auto mb-2 text-amber-500/50" />
            <p className="text-sm font-medium text-amber-600 dark:text-amber-400">
              {t("settings.connectors.serverOffline.title")}
            </p>
            <p className="text-xs text-muted-foreground mt-1 max-w-sm mx-auto">
              {t("settings.connectors.serverOffline.prefix")}{" "}
              <code className="bg-muted px-1 rounded">levh serve</code>{" "}
              {t("settings.connectors.serverOffline.suffix")}
            </p>
          </div>
        )}

        {/* Connector grid */}
        <div className="space-y-3">
          {categories.map((cat) => (
            <div key={cat}>
              <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/70 mb-2">
                {t(cat)}
              </p>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                {connectors
                  .filter(
                    (c) => (CONNECTOR_META[c.name]?.category || "settings.connectors.category.other") === cat
                  )
                  .map((c) => {
                    const meta = CONNECTOR_META[c.name];
                    const Icon = meta?.icon || Plug;
                    const isActive = selConnector === c.name;
                    const lastSync = lastSyncFor(c.name);
                    return (
                      <button
                        key={c.name}
                        onClick={() => {
                          setSelConnector(c.name);
                          setConnectorConfig({});
                          setImportResult("");
                        }}
                        className={`connector-card ${isActive ? "active" : ""}`}
                      >
                        <div className="flex items-center gap-2.5">
                          <div
                            className={`h-8 w-8 rounded-lg flex items-center justify-center bg-muted ${
                              meta?.color || "text-muted-foreground"
                            }`}
                          >
                            <Icon className="h-4 w-4" />
                          </div>
                          <div className="text-left min-w-0">
                            <span className="text-sm font-medium block truncate">
                              {c.name.replace(/_/g, " ")}
                            </span>
                            {lastSync && (
                              <span className="text-[10px] text-muted-foreground flex items-center gap-1">
                                <Check className="h-2.5 w-2.5 text-emerald-500" />
                                {t("settings.connectors.storedCount", { count: lastSync.total_stored })}
                              </span>
                            )}
                          </div>
                        </div>
                      </button>
                    );
                  })}
              </div>
            </div>
          ))}
        </div>

        {/* Active connector config */}
        {activeConnector && (
          <div className="space-y-3 p-4 rounded-xl border bg-muted/30">
            <div className="flex items-center gap-2">
              {activeMeta && (
                <div
                  className={`h-6 w-6 rounded flex items-center justify-center ${
                    activeMeta.color
                  }`}
                >
                  <activeMeta.icon className="h-3.5 w-3.5" />
                </div>
              )}
              <div>
                <p className="text-sm font-medium">
                  {selConnector.replace(/_/g, " ")}
                </p>
                <p className="text-[11px] text-muted-foreground">
                  {activeMeta
                    ? t(activeMeta.description)
                    : activeConnector.description}
                </p>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {activeConnector.required_config_keys.map((key) =>
                key in FILE_CONFIG_KEYS ? (
                  <div key={key} className="space-y-1">
                    <Label className="text-xs">{key}</Label>
                    <Input
                      type="file"
                      accept={FILE_CONFIG_KEYS[key]}
                      disabled={uploadingKey === key}
                      className="cursor-pointer file:mr-3 file:rounded file:border-0 file:bg-muted file:px-2 file:py-1 file:text-xs"
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        if (file) uploadConnectorFile(key, file);
                      }}
                    />
                    <p className="text-[11px] text-muted-foreground">
                      {uploadingKey === key
                        ? t("settings.connectors.upload.uploading")
                        : connectorConfig[key]
                        ? t("settings.connectors.upload.ready", { path: connectorConfig[key] })
                        : t("settings.connectors.upload.pickFile")}
                    </p>
                  </div>
                ) : (
                  <div key={key} className="space-y-1">
                    <Label className="text-xs">{key}</Label>
                    <Input
                      placeholder={
                        key === "vault_path" || key === "directory"
                          ? "/absolute/path"
                          : key === "repos"
                          ? "owner/repo, owner/other"
                          : key
                      }
                      value={connectorConfig[key] ?? ""}
                      onChange={(e) =>
                        setConnectorConfig((prev) => ({
                          ...prev,
                          [key]: e.target.value,
                        }))
                      }
                    />
                  </div>
                )
              )}
              <div className="space-y-1">
                <Label className="text-xs">{t("settings.connectors.projectOptional")}</Label>
                <Input
                  value={importProject}
                  onChange={(e) => setImportProject(e.target.value)}
                  placeholder={t("settings.connectors.projectPlaceholder")}
                />
              </div>
            </div>

            {selConnector === "git" && (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 pt-1">
                <span className="text-[11px] text-muted-foreground">{t("settings.connectors.git.include")}</span>
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
                  <input
                    type="checkbox"
                    checked={gitHistory}
                    onChange={(e) => setGitHistory(e.target.checked)}
                    className="rounded"
                  />
                  {t("settings.connectors.git.fileHistory")}
                </label>
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
                  <input
                    type="checkbox"
                    checked={gitBlame}
                    onChange={(e) => setGitBlame(e.target.checked)}
                    className="rounded"
                  />
                  {t("settings.connectors.git.blameSummary")}
                </label>
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
                  <input
                    type="checkbox"
                    checked={gitSnapshot}
                    onChange={(e) => setGitSnapshot(e.target.checked)}
                    className="rounded"
                  />
                  {t("settings.connectors.git.archSnapshot")}
                </label>
              </div>
            )}

            <div className="flex items-center justify-between pt-2">
              <div className="flex items-center gap-4">
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
                  <input
                    type="checkbox"
                    checked={useGate}
                    onChange={(e) => setUseGate(e.target.checked)}
                    className="rounded"
                  />
                  {t("settings.connectors.admissionGate")}
                </label>
                <label
                  className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer"
                  title={t("settings.connectors.background.help")}
                >
                  <input
                    type="checkbox"
                    checked={background}
                    onChange={(e) => setBackground(e.target.checked)}
                    className="rounded"
                  />
                  {t("settings.connectors.background.label")}
                </label>
              </div>
              <Button
                onClick={runImport}
                disabled={importing}
                size="sm"
                className="gap-2"
              >
                {importing ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Upload className="h-3.5 w-3.5" />
                )}
                {t("settings.connectors.runImport")}
              </Button>
            </div>

            {importResult && (
              <p className="text-xs text-muted-foreground bg-muted/50 rounded-lg p-2">
                {importResult}
              </p>
            )}
          </div>
        )}

        {/* Sync history */}
        {syncState.length > 0 && (
          <div className="pt-3 border-t">
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/70 mb-2">
              {t("settings.connectors.syncHistory")}
            </p>
            <div className="space-y-1.5">
              {syncState.map((s) => {
                const meta = CONNECTOR_META[s.connector];
                const Icon = meta?.icon || Plug;
                return (
                  <div
                    key={s.source_key}
                    className="flex items-center gap-2 text-xs"
                  >
                    <Icon className={`h-3.5 w-3.5 ${meta?.color || "text-muted-foreground"}`} />
                    <span className="font-medium">{s.connector.replace(/_/g, " ")}</span>
                    {s.project && (
                      <Badge variant="outline" className="text-[10px]">
                        {s.project}
                      </Badge>
                    )}
                    <span className="text-muted-foreground ml-auto">
                      {t("settings.connectors.storedCount", { count: s.total_stored })} ·{" "}
                      {new Date(s.last_synced_at).toLocaleDateString("en-GB", {
                        day: "numeric",
                        month: "short",
                      })}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
        {/* Background jobs */}
        {jobs.length > 0 && (
          <div className="pt-3 border-t">
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/70 mb-2">
              {t("settings.connectors.background.jobs")}
            </p>
            <div className="space-y-1.5">
              {jobs.slice(0, 5).map((j) => {
                const busy = j.status === "pending" || j.status === "running";
                return (
                  <div
                    key={j.job_id}
                    className="flex items-center gap-2 text-xs"
                  >
                    {busy ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                    ) : j.status === "done" ? (
                      <Check className="h-3.5 w-3.5 text-emerald-500" />
                    ) : (
                      <Plug className="h-3.5 w-3.5 text-red-500" />
                    )}
                    <span className="font-medium">{j.connector.replace(/_/g, " ")}</span>
                    {j.project && (
                      <Badge variant="outline" className="text-[10px]">
                        {j.project}
                      </Badge>
                    )}
                    <span className="text-muted-foreground ml-auto">
                      {j.status === "done" && j.result
                        ? t("settings.connectors.storedCount", { count: j.result.stored })
                        : j.status === "error"
                        ? j.error ?? t("settings.connectors.failed")
                        : j.status}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
