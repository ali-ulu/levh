"use client";

import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Download, Loader2, ShieldCheck, Upload } from "lucide-react";

export function BackupRestore() {
    const t = useT();
    const [backupPass, setBackupPass] = useState("");
    const [backingUp, setBackingUp] = useState(false);
    const [restorePass, setRestorePass] = useState("");
    const [restoreReplace, setRestoreReplace] = useState(false);
    const [restoring, setRestoring] = useState(false);
    const [restoreResult, setRestoreResult] = useState("");
    const backupFileRef = useRef<HTMLInputElement>(null);
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
        alert(t("settings.backupRestore.backupFailed", { error: e instanceof Error ? e.message : String(e) }));
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
          t("settings.backupRestore.restoreSuccess", {
            memories: r.memories,
            sessions: r.sessions,
            mode: t(
              r.replace
                ? "settings.backupRestore.mode.replaced"
                : "settings.backupRestore.mode.merged"
            ),
          })
        );
      } catch (e) {
        setRestoreResult(t("settings.backupRestore.restoreFailed", { error: e instanceof Error ? e.message : String(e) }));
      }
      setRestoring(false);
    };

  return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <ShieldCheck className="h-4 w-4" />
            {t("settings.backupRestore.title")}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-xs text-muted-foreground">
            {t("settings.backupRestore.description")}
          </p>

          <div className="flex flex-wrap items-end gap-2">
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">
                {t("settings.backupRestore.backupPassLabel")}
              </Label>
              <Input
                type="password"
                className="w-64"
                placeholder={t("settings.backupRestore.backupPassPlaceholder")}
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
              {t("settings.backupRestore.download")}
            </Button>
          </div>

          <div className="space-y-2 pt-3 border-t">
            <Label className="text-xs text-muted-foreground">{t("settings.backupRestore.restoreFileLabel")}</Label>
            <div className="flex flex-wrap items-end gap-2">
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">
                  {t("settings.backupRestore.restorePassLabel")}
                </Label>
                <Input
                  type="password"
                  className="w-64"
                  placeholder={t("settings.backupRestore.restorePassPlaceholder")}
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
                {t("settings.backupRestore.replaceLabel")}
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
                {t("settings.backupRestore.chooseFileRestore")}
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
                {t("settings.backupRestore.replaceWarning")}
              </p>
            )}
            {restoreResult && (
              <p className="text-xs text-muted-foreground">{restoreResult}</p>
            )}
          </div>
        </CardContent>
      </Card>
  );
}
