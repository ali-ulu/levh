"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { Project } from "@/types";
import { Check, Copy, Download, FileText, FolderGit2, Loader2 } from "lucide-react";

const ALL_PROJECTS = "__all__";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);

  // Context file dialog
  const [dialogOpen, setDialogOpen] = useState(false);
  const [ctxProject, setCtxProject] = useState<string>(ALL_PROJECTS);
  const [ctxStyle, setCtxStyle] = useState<"claude" | "cursor">("claude");
  const [ctxContent, setCtxContent] = useState("");
  const [ctxFilename, setCtxFilename] = useState("CLAUDE.md");
  const [generating, setGenerating] = useState(false);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.listProjects();
      setProjects(r.projects);
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const generate = async (project: string, style: "claude" | "cursor") => {
    setGenerating(true);
    setCopied(false);
    try {
      const r = await api.generateContextFile(project === ALL_PROJECTS ? null : project, style);
      setCtxContent(r.content);
      setCtxFilename(r.filename);
    } catch (e) {
      setCtxContent(`Failed to generate: ${e instanceof Error ? e.message : e}`);
    }
    setGenerating(false);
  };

  const openDialog = (project: string) => {
    setCtxProject(project);
    setCtxStyle("claude");
    setDialogOpen(true);
    generate(project, "claude");
  };

  const copy = async () => {
    await navigator.clipboard.writeText(ctxContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const download = () => {
    const blob = new Blob([ctxContent], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = ctxFilename;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">Projects</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Memories grouped by workspace. Generate a CLAUDE.md or .cursorrules file
            so every session starts with your project&apos;s memory built in.
          </p>
        </div>
        <Button variant="outline" onClick={() => openDialog(ALL_PROJECTS)}>
          <FileText className="h-4 w-4 mr-2" />
          Context file (all)
        </Button>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : projects.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground space-y-2">
            <FolderGit2 className="h-8 w-8 mx-auto opacity-40" />
            <p>No projects yet.</p>
            <p>
              Store memories with a <code className="text-xs bg-muted px-1 rounded">project</code>{" "}
              name — from the dashboard, the <code className="text-xs bg-muted px-1 rounded">store_memory</code> MCP
              tool, or <code className="text-xs bg-muted px-1 rounded">levh capture</code> (auto-detects
              your git repo).
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {projects.map((p) => (
            <Card key={p.name}>
              <CardHeader className="pb-2">
                <CardTitle className="text-base flex items-center gap-2">
                  <FolderGit2 className="h-4 w-4 text-primary" />
                  {p.name}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="text-sm text-muted-foreground">
                  {p.memory_count} memories
                  {p.last_used && (
                    <> · last used {new Date(p.last_used).toLocaleDateString("en-GB")}</>
                  )}
                </div>
                <div className="flex gap-2">
                  <Button asChild variant="outline" size="sm">
                    <Link href={`/memories/?project=${encodeURIComponent(p.name)}`}>Browse</Link>
                  </Button>
                  <Button size="sm" onClick={() => openDialog(p.name)}>
                    <FileText className="h-3.5 w-3.5 mr-1.5" />
                    Context file
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Context file dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              Context File — {ctxProject === ALL_PROJECTS ? "all projects" : ctxProject}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Select
                value={ctxStyle}
                onValueChange={(v) => {
                  const style = v as "claude" | "cursor";
                  setCtxStyle(style);
                  generate(ctxProject, style);
                }}
              >
                <SelectTrigger className="w-44">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="claude">CLAUDE.md</SelectItem>
                  <SelectItem value="cursor">.cursorrules</SelectItem>
                </SelectContent>
              </Select>
              <Button variant="outline" size="sm" onClick={copy} disabled={generating || !ctxContent}>
                {copied ? <Check className="h-3.5 w-3.5 mr-1.5" /> : <Copy className="h-3.5 w-3.5 mr-1.5" />}
                {copied ? "Copied" : "Copy"}
              </Button>
              <Button variant="outline" size="sm" onClick={download} disabled={generating || !ctxContent}>
                <Download className="h-3.5 w-3.5 mr-1.5" />
                Download {ctxFilename}
              </Button>
            </div>
            {generating ? (
              <div className="flex justify-center py-8">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : (
              <pre className="text-xs bg-muted rounded-lg p-3 max-h-96 overflow-auto whitespace-pre-wrap">
                {ctxContent}
              </pre>
            )}
            <p className="text-xs text-muted-foreground">
              Drop this file into your repo root — Claude Code, Cursor, and other clients read it
              automatically at session start. Regenerate whenever your memories change, or run{" "}
              <code className="bg-muted px-1 rounded">levh context -o CLAUDE.md</code>.
            </p>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
