"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { ServerConfig } from "@/types";
import { Check, Copy, ExternalLink, Sparkles } from "lucide-react";

interface ConnectClientProps {
  config: ServerConfig | null;
  client: string;
  setClient: (value: string) => void;
}

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
  {
    id: "claude_desktop",
    name: "Claude Desktop",
    path: "~/Library/Application Support/Claude/claude_desktop_config.json",
    color: "bg-orange-500/10 text-orange-600 dark:text-orange-400",
    letter: "C",
  },
  {
    id: "claude_code",
    name: "Claude Code",
    path: "~/.claude.json",
    color: "bg-orange-500/10 text-orange-600 dark:text-orange-400",
    letter: "C",
  },
  {
    id: "cursor",
    name: "Cursor",
    path: ".cursor/mcp.json (project root)",
    color: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
    letter: "Cu",
  },
  {
    id: "windsurf",
    name: "Windsurf",
    path: "~/.codeium/windsurf/mcp_config.json",
    color: "bg-cyan-500/10 text-cyan-600 dark:text-cyan-400",
    letter: "W",
  },
  {
    id: "cline",
    name: "VS Code (Cline)",
    path: ".vscode/mcp.json (project root)",
    color: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400",
    letter: "V",
  },
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
      className="gap-1.5"
    >
      {copied ? (
        <Check className="h-3.5 w-3.5" />
      ) : (
        <Copy className="h-3.5 w-3.5" />
      )}
      {copied ? "Copied!" : "Copy config"}
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
        <p className="text-xs text-muted-foreground">
          Set up MCP (Model Context Protocol) to give your AI tools persistent memory.
          Select your client below and copy the config.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Client selector grid */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
          {CLIENTS.map((c) => (
            <button
              key={c.id}
              onClick={() => setClient(c.id)}
              className={`client-card ${client === c.id ? "active" : ""}`}
            >
              <div
                className={`h-8 w-8 rounded-lg flex items-center justify-center font-semibold text-sm ${c.color}`}
              >
                {c.letter}
              </div>
              <span className="text-xs font-medium mt-1">{c.name}</span>
            </button>
          ))}
        </div>

        {/* Install path + copy */}
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1 flex-1 min-w-48">
            <Label className="text-xs text-muted-foreground">
              LEVH install path
            </Label>
            <Input value={installPath} onChange={(e) => setInstallPath(e.target.value)} />
          </div>
          <CopyButton text={MCP_SNIPPET(installPath)} />
        </div>

        {/* Config file location */}
        <p className="text-xs text-muted-foreground">
          Add to{" "}
          <code className="bg-muted px-1.5 py-0.5 rounded text-[11px]">
            {selectedClient.path}
          </code>
        </p>

        {/* Code block */}
        <div className="relative group">
          <pre className="text-xs bg-muted rounded-xl p-4 overflow-auto border font-mono leading-relaxed">
            {MCP_SNIPPET(installPath)}
          </pre>
        </div>

        {/* CLI alternative */}
        <p className="text-xs text-muted-foreground">
          Or generate configs from the CLI:{" "}
          <code className="bg-muted px-1.5 py-0.5 rounded text-[11px]">
            levh mcp config {client}
          </code>
        </p>
      </CardContent>
    </Card>
  );
}
