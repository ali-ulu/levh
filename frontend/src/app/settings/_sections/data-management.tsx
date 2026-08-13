"use client";

import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { Download, Loader2, Upload } from "lucide-react";

export function DataManagement() {
    const [exporting, setExporting] = useState(false);
    const [importingJson, setImportingJson] = useState(false);
    const [dedupeBusy, setDedupeBusy] = useState(false);
    const [dedupeResult, setDedupeResult] = useState("");
    const [consolidateSimBusy, setConsolidateSimBusy] = useState(false);
    const [consolidateSimResult, setConsolidateSimResult] = useState("");
    const [consolidateBusy, setConsolidateBusy] = useState(false);
    const [consolidateResult, setConsolidateResult] = useState("");
    const fileInputRef = useRef<HTMLInputElement>(null);
    const [fullExportBusy, setFullExportBusy] = useState<"" | "json" | "sqlite" | "pdf">("");
    const [fullExportError, setFullExportError] = useState("");
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
    const downloadFullExport = async (format: "json" | "sqlite" | "pdf") => {
      setFullExportBusy(format);
      setFullExportError("");
      try {
        const { blob, filename } = await api.exportFull(format);
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
      } catch (e) {
        setFullExportError(e instanceof Error ? e.message : String(e));
      }
      setFullExportBusy("");
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

  return (
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

          <div className="pt-2 border-t space-y-1">
            <p className="text-xs text-muted-foreground">
              Full audit export — memories, entity graph, trust scores, and conflict candidates
              in one file. For auditing or backing up everything, not just memories.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                onClick={() => downloadFullExport("json")}
                disabled={fullExportBusy !== ""}
              >
                {fullExportBusy === "json" ? (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                ) : (
                  <Download className="h-4 w-4 mr-2" />
                )}
                Full export (JSON)
              </Button>
              <Button
                variant="outline"
                onClick={() => downloadFullExport("sqlite")}
                disabled={fullExportBusy !== ""}
              >
                {fullExportBusy === "sqlite" ? (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                ) : (
                  <Download className="h-4 w-4 mr-2" />
                )}
                Full export (SQLite)
              </Button>
              <Button
                variant="outline"
                onClick={() => downloadFullExport("pdf")}
                disabled={fullExportBusy !== ""}
              >
                {fullExportBusy === "pdf" ? (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                ) : (
                  <Download className="h-4 w-4 mr-2" />
                )}
                Audit report (PDF)
              </Button>
            </div>
            {fullExportError && (
              <span className="text-xs text-destructive">{fullExportError}</span>
            )}
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
  );
}
