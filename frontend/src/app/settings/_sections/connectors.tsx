"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import type { Connector, SyncState } from "@/types";
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
    description: "Import text files from a local directory",
    category: "Files",
  },
  calendar: {
    icon: Calendar,
    color: "text-emerald-500",
    description: "Import calendar events from .ics files",
    category: "Productivity",
  },
  email: {
    icon: Mail,
    color: "text-purple-500",
    description: "Import emails from .mbox or .eml files",
    category: "Productivity",
  },
  transcripts: {
    icon: Music,
    color: "text-amber-500",
    description: "Import meeting transcripts from .vtt/.srt files",
    category: "Productivity",
  },
  obsidian: {
    icon: NotepadText,
    color: "text-violet-500",
    description: "Import notes from an Obsidian vault",
    category: "Notes",
  },
  notion: {
    icon: FileText,
    color: "text-gray-500",
    description: "Import pages from a Notion workspace",
    category: "Notes",
  },
  github: {
    icon: GitBranch,
    color: "text-gray-700 dark:text-gray-300",
    description: "Import issues and PRs from GitHub repositories",
    category: "Development",
  },
};

const CATEGORY_COLORS: Record<string, string> = {
  Files: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
  Productivity: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  Notes: "bg-violet-500/10 text-violet-600 dark:text-violet-400",
  Development: "bg-gray-500/10 text-gray-600 dark:text-gray-400",
};

export function Connectors() {
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [selConnector, setSelConnector] = useState("local_files");
  const [connectorConfig, setConnectorConfig] = useState<Record<string, string>>({});
  const [importProject, setImportProject] = useState("");
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState("");
  const [useGate, setUseGate] = useState(true);
  const [syncState, setSyncState] = useState<SyncState[]>([]);
  const [uploadingKey, setUploadingKey] = useState("");

  useEffect(() => {
    api
      .listConnectors()
      .then((r) => setConnectors(r.connectors))
      .catch(() => setConnectors([]));
    api
      .connectorSyncState()
      .then((r) => setSyncState(r.sync_state))
      .catch(() => setSyncState([]));
  }, []);

  const activeConnector = connectors.find((c) => c.name === selConnector);
  const activeMeta = CONNECTOR_META[selConnector];

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
      if (useGate) {
        const r = await api.connectorSync(
          selConnector,
          cfg,
          importProject.trim() || undefined,
          true
        );
        setImportResult(
          `Stored ${r.stored} of ${r.fetched} items from ${r.connector} ` +
            `(${r.duplicates} duplicates skipped, ${r.redacted} secrets redacted` +
            (r.held ? `, ${r.held} held for review` : "") +
            (r.errors ? `, ${r.errors} errors` : "") +
            ")."
        );
      } else {
        const r = await api.connectorImport(
          selConnector,
          cfg,
          importProject.trim() || undefined
        );
        setImportResult(
          `Imported ${r.stored} of ${r.fetched} items from ${r.connector}.`
        );
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
        `Uploaded ${r.filename} (${(r.bytes / 1024).toFixed(0)} KB). Now run the import.`
      );
    } catch (e) {
      setImportResult(e instanceof Error ? e.message : "Upload failed");
    }
    setUploadingKey("");
  };

  // Group connectors by category
  const categories = Array.from(new Set(connectors.map((c) => CONNECTOR_META[c.name]?.category || "Other")));

  const lastSyncFor = (name: string) =>
    syncState.find((s) => s.connector === name);

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Plug className="h-4 w-4" />
          Connectors
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          Bring in real data from calendar, email, Notion, GitHub, and more. Each connector
          imports into your local memory store — nothing leaves your machine.
        </p>
      </CardHeader>
      <CardContent className="space-y-5">
        {/* Connector grid */}
        <div className="space-y-3">
          {categories.map((cat) => (
            <div key={cat}>
              <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/70 mb-2">
                {cat}
              </p>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                {connectors
                  .filter(
                    (c) => (CONNECTOR_META[c.name]?.category || "Other") === cat
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
                                {lastSync.total_stored} stored
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
                  {activeMeta?.description || activeConnector.description}
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
                        ? "Uploading…"
                        : connectorConfig[key]
                        ? `Ready: ${connectorConfig[key]}`
                        : "Pick the exported file"}
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
                <Label className="text-xs">Project (optional)</Label>
                <Input
                  value={importProject}
                  onChange={(e) => setImportProject(e.target.value)}
                  placeholder="my-repo"
                />
              </div>
            </div>

            <div className="flex items-center justify-between pt-2">
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
                <input
                  type="checkbox"
                  checked={useGate}
                  onChange={(e) => setUseGate(e.target.checked)}
                  className="rounded"
                />
                Route through admission gate
              </label>
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
                Run import
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
              Sync history
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
                      {s.total_stored} stored ·{" "}
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
      </CardContent>
    </Card>
  );
}
