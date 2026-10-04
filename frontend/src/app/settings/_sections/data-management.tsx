"use client";

import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Download, Loader2, Upload } from "lucide-react";

export function DataManagement() {
    const t = useT();
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
        alert(t(
          r.imported === 1
            ? "settings.dataManagement.import.success.one"
            : "settings.dataManagement.import.success.other",
          { count: r.imported },
        ));
      } catch (e) {
        alert(t("settings.dataManagement.import.failed", { error: e instanceof Error ? e.message : String(e) }));
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
            ? t("settings.dataManagement.dedupe.removed", { count: r.removed ?? 0 })
            : t("settings.dataManagement.dedupe.found", { count: r.duplicates ?? 0 })
        );
      } catch (e) {
        setDedupeResult(e instanceof Error ? e.message : t("settings.dataManagement.dedupe.failed"));
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
t("settings.dataManagement.similar.applied", { clusters: r.consolidated ?? 0, memories: r.archived ?? 0 })
          );
        } else {
          setConsolidateSimResult(
            r.clusters_found === 0
              ? t("settings.dataManagement.similar.none")
              : t("settings.dataManagement.similar.found", {
                  clusters: r.clusters_found,
                  memories: r.clusters.reduce((sum, cluster) => sum + cluster.size, 0),
                })
          );
        }
      } catch (e) {
        setConsolidateSimResult(e instanceof Error ? e.message : t("settings.dataManagement.consolidation.failed"));
      }
      setConsolidateSimBusy(false);
    };
    const runConsolidate = async () => {
      setConsolidateBusy(true);
      setConsolidateResult("");
      try {
        const r = await api.consolidate();
        setConsolidateResult(t("settings.dataManagement.shortTerm.promoted", { count: r.consolidated }));
      } catch (e) {
        setConsolidateResult(e instanceof Error ? e.message : t("settings.dataManagement.consolidation.failed"));
      }
      setConsolidateBusy(false);
    };

  return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Download className="h-4 w-4" />
            {t("settings.dataManagement.title")}
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
              {t("settings.dataManagement.exportMemories")}
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
              {t("settings.dataManagement.importJson")}
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
              {t("settings.dataManagement.fullExport.description")}
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
                {t("settings.dataManagement.fullExport.json")}
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
                {t("settings.dataManagement.fullExport.sqlite")}
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
                {t("settings.dataManagement.fullExport.pdf")}
              </Button>
            </div>
            {fullExportError && (
              <span className="text-xs text-destructive">{fullExportError}</span>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2 pt-2 border-t">
            <Button variant="outline" onClick={runConsolidate} disabled={consolidateBusy}>
              {consolidateBusy && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              {t("settings.dataManagement.shortTerm.action")}
            </Button>
            {consolidateResult && (
              <span className="text-xs text-muted-foreground">{consolidateResult}</span>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2 pt-2 border-t">
            <Button variant="outline" onClick={() => runDedupe(false)} disabled={dedupeBusy}>
              {dedupeBusy && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              {t("settings.dataManagement.dedupe.find")}
            </Button>
            <Button variant="outline" onClick={() => runDedupe(true)} disabled={dedupeBusy}>
              {t("settings.dataManagement.dedupe.remove")}
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
              {t("settings.dataManagement.similar.preview")}
            </Button>
            <Button
              variant="outline"
              onClick={() => runConsolidateSimilar(true)}
              disabled={consolidateSimBusy}
            >
              {t("settings.dataManagement.similar.consolidate")}
            </Button>
            {consolidateSimResult && (
              <span className="text-xs text-muted-foreground">{consolidateSimResult}</span>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            {t("settings.dataManagement.similar.help")}
          </p>
        </CardContent>
      </Card>
  );
}
