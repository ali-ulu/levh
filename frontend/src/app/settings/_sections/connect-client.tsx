"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { ServerConfig } from "@/types";
import { Check, Copy, Sparkles } from "lucide-react";

interface ConnectClientProps {
  config: ServerConfig | null;
  client: string;
  setClient: (value: string) => void;
}


// The snippet a user pastes into their client's MCP config.
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

export function ConnectClient({ config, client, setClient }: ConnectClientProps) {
    const [installPath, setInstallPath] = useState("/path/to/levh-new");
    const selectedClient = CLIENTS.find((c) => c.id === client)!;

  return (
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
  );
}
