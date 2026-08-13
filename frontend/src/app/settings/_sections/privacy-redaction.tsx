"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { Loader2, ShieldAlert } from "lucide-react";

export function PrivacyRedaction() {
    const [secretsScanBusy, setSecretsScanBusy] = useState(false);
    const [secretsAudit, setSecretsAudit] = useState<{
      scanned: number;
      flagged: number;
      items: { id: string; secret_types: string[]; preview: string }[];
    } | null>(null);
    const [secretsError, setSecretsError] = useState("");
    const [redactAllBusy, setRedactAllBusy] = useState(false);
    const [redactAllResult, setRedactAllResult] = useState("");
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

  return (
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
                        {item.secret_types.join(", ")}
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
  );
}
