"use client";

import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { Download, Loader2, ShieldCheck, Upload } from "lucide-react";

export function BackupRestore() {
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

  return (
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
  );
}
