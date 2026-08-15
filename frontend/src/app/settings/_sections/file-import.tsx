"use client";

import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { FileUp, Loader2 } from "lucide-react";

function toBase64(buf: ArrayBuffer): string {
  let binary = "";
  const bytes = new Uint8Array(buf);
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + chunk)));
  }
  return btoa(binary);
}

export function FileImport() {
  const [project, setProject] = useState("");
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  const importFiles = async (files: FileList) => {
    setBusy(true);
    const lines: string[] = [];
    for (const file of Array.from(files)) {
      try {
        const buf = await file.arrayBuffer();
        const content_b64 = toBase64(buf);
        const r = await api.importFile(file.name, content_b64, project || undefined);
        lines.push(
          `${file.name}: ${r.memories_created} memor${r.memories_created === 1 ? "y" : "ies"}` +
            (r.warnings.length ? ` (${r.warnings.join("; ")})` : "")
        );
      } catch (e) {
        lines.push(`${file.name}: failed — ${e instanceof Error ? e.message : e}`);
      }
    }
    setResults(lines);
    setBusy(false);
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <FileUp className="h-4 w-4" />
          Import Files
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          Drop in any file — text, PDF, Word, Excel, or a zip of several — and it&apos;s
          turned into memories. Text is extracted where possible; other formats
          still record that the file arrived rather than being silently dropped.
          No size limit is enforced by the app itself.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Project (optional)</Label>
            <Input
              className="w-56"
              placeholder="e.g. levh"
              value={project}
              onChange={(e) => setProject(e.target.value)}
            />
          </div>
          <Button variant="outline" onClick={() => inputRef.current?.click()} disabled={busy}>
            {busy ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : (
              <FileUp className="h-4 w-4 mr-2" />
            )}
            Choose file(s) to import
          </Button>
          <input
            ref={inputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              const files = e.target.files;
              if (files && files.length) importFiles(files);
              e.target.value = "";
            }}
          />
        </div>
        {results.length > 0 && (
          <ul className="text-xs text-muted-foreground space-y-0.5">
            {results.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
