"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import type { Connector, SyncState } from "@/types";
import { Loader2, Plug, Upload } from "lucide-react";

// Connector config keys that name a single file the server has to read.
// Directory keys (directory, vault_path) stay typed — a browser cannot hand
// over a folder, and credential keys are not paths at all.
const FILE_CONFIG_KEYS: Record<string, string> = {
  ics_path: ".ics",
  mbox_path: ".mbox,.eml",
  transcript_path: ".vtt,.srt,.txt",
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
    // Loaded here rather than by the page: this card is the only thing that
    // shows either list, and a section that renders its own data cannot be
    // left empty by a parent that forgot to fetch.
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
    const uploadConnectorFile = async (key: string, file: File) => {
      setUploadingKey(key);
      setImportResult("");
      try {
        const buffer = await file.arrayBuffer();
        // btoa() over a big string blows the argument limit, so chunk it.
        const bytes = new Uint8Array(buffer);
        const parts: string[] = [];
        for (let i = 0; i < bytes.length; i += 0x8000) {
          parts.push(String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + 0x8000))));
        }
        const binary = parts.join("");
        const r = await api.connectorUpload(file.name, btoa(binary));
        setConnectorConfig((prev) => ({ ...prev, [key]: r.path }));
        setImportResult(`Uploaded ${r.filename} (${(r.bytes / 1024).toFixed(0)} KB). Now run the import.`);
      } catch (e) {
        setImportResult(e instanceof Error ? e.message : "Upload failed");
      }
      setUploadingKey("");
    };

  return (
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
          <p className="text-xs text-muted-foreground">
            Pick a connector, then fill what it asks for: calendar, email and transcripts take a
            file you upload here; Obsidian and local files take a folder path on this machine;
            Notion and GitHub take an API credential. Everything is read locally — nothing is
            sent anywhere else.
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
                      <p className="text-xs text-muted-foreground">
                        {uploadingKey === key
                          ? "Uploading…"
                          : connectorConfig[key]
                          ? `Ready: ${connectorConfig[key]}`
                          : "Pick the exported file — it is copied to this machine's LEVH folder, then imported."}
                      </p>
                    </div>
                  ) : (
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
                  )
                )}
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
  );
}
