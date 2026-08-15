"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ServerConfig } from "@/types";
import { AccessToken } from "./_sections/access-token";
import { AdmissionGate } from "./_sections/admission-gate";
import { BackupRestore } from "./_sections/backup-restore";
import { ConnectClient } from "./_sections/connect-client";
import { Connectors } from "./_sections/connectors";
import { DataManagement } from "./_sections/data-management";
import { FileImport } from "./_sections/file-import";
import { PrivacyRedaction } from "./_sections/privacy-redaction";
import { RecallQuality } from "./_sections/recall-quality";
import { ServerConfiguration } from "./_sections/server-configuration";
import { TrustProvenance } from "./_sections/trust-provenance";

export default function SettingsPage() {
  // Only what more than one section needs lives here. Everything else — the
  // connector list, the audit results, the benchmark — is loaded by the
  // section that shows it, so a section cannot render empty because some
  // other component forgot to fetch on its behalf.
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const [client, setClient] = useState("claude_desktop");

  useEffect(() => {
    api
      .config()
      .then(setConfig)
      .catch(() => setConfig(null));
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Server configuration, AI client setup, imports, and data management.
        </p>
      </div>

      <ServerConfiguration config={config} />
      <AccessToken />
      <RecallQuality config={config} />
      <AdmissionGate />
      <PrivacyRedaction />
      <TrustProvenance />
      <ConnectClient config={config} client={client} setClient={setClient} />
      <Connectors />
      <DataManagement />
      <FileImport />
      <BackupRestore />
    </div>
  );
}
