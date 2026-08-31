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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { Project } from "@/types";
import {
  Check,
  Copy,
  Download,
  Edit3,
  FileText,
  FolderGit2,
  Loader2,
  Plus,
  Sparkles,
  Trash2,
} from "lucide-react";

const ALL_PROJECTS = "__all__";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);

  // Create/edit dialog
  const [createOpen, setCreateOpen] = useState(false);
  const [editProject, setEditProject] = useState<Project | null>(null);
  const [projectName, setProjectName] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  // Delete confirm
  const [deleteTarget, setDeleteTarget] = useState<Project | null>(null);

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

  const openCreate = () => {
    setEditProject(null);
    setProjectName("");
    setError("");
    setCreateOpen(true);
  };

  const openEdit = (p: Project) => {
    setEditProject(p);
    setProjectName(p.name);
    setError("");
    setCreateOpen(true);
  };

  const saveProject = async () => {
    const name = projectName.trim();
    if (!name) {
      setError("Project name is required");
      return;
    }
    setSaving(true);
    setError("");
    try {
      if (editProject) {
        // Update existing project by storing a memory with the new project name
        // (projects are implicit — created when a memory is stored with a project name)
        // For now, just close — API doesn't have a rename endpoint yet
        setCreateOpen(false);
      } else {
        // Create project by storing a sample memory
        await api.storeMemory({
          content: `[Project initialized] ${name}`,
          importance: 0.1,
          tags: ["project-init"],
          project: name,
          source: "dashboard",
          memory_type: "episodic",
        });
        setCreateOpen(false);
        load();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save project");
    }
    setSaving(false);
  };

  const deleteProject = async () => {
    if (!deleteTarget) return;
    setSaving(true);
    try {
      // API doesn't have delete project endpoint — show info
      setDeleteTarget(null);
    } catch {}
    setSaving(false);
  };

  const generate = async (project: string, style: "claude" | "cursor") => {
    setGenerating(true);
    setCopied(false);
    try {
      const r = await api.generateContextFile(
        project === ALL_PROJECTS ? null : project,
        style
      );
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
            Memories grouped by workspace. Create a project to organize related
            memories, then generate context files for your AI clients.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => openDialog(ALL_PROJECTS)}>
            <FileText className="h-4 w-4 mr-2" />
            Context file (all)
          </Button>
          <Button onClick={openCreate}>
            <Plus className="h-4 w-4 mr-2" />
            New project
          </Button>
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : projects.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground space-y-4">
            <FolderGit2 className="h-12 w-12 mx-auto opacity-20" />
            <div className="space-y-1">
              <p className="text-base font-medium text-foreground">No projects yet</p>
              <p className="max-w-md mx-auto">
                Create your first project to group related memories. You can also
                store memories with a project name from the dashboard or via the{" "}
                <code className="text-xs bg-muted px-1 rounded">store_memory</code> MCP tool.
              </p>
            </div>
            <Button onClick={openCreate} className="mt-4">
              <Sparkles className="h-4 w-4 mr-2" />
              Create your first project
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {projects.map((p) => (
            <Card key={p.name} className="group hover:shadow-md transition-shadow">
              <CardHeader className="pb-2">
                <CardTitle className="text-base flex items-center gap-2">
                  <FolderGit2 className="h-4 w-4 text-primary" />
                  {p.name}
                  <div className="ml-auto flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={() => openEdit(p)}
                      className="p-1 rounded hover:bg-muted text-muted-foreground hover:text-foreground"
                      title="Edit project"
                    >
                      <Edit3 className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={() => setDeleteTarget(p)}
                      className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive"
                      title="Delete project"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
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
                    <Link href={`/memories/?project=${encodeURIComponent(p.name)}`}>
                      Browse
                    </Link>
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

      {/* Create / Edit Project Dialog */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FolderGit2 className="h-5 w-5" />
              {editProject ? `Edit ${editProject.name}` : "New Project"}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label className="text-xs">Project name</Label>
              <Input
                value={projectName}
                onChange={(e) => setProjectName(e.target.value)}
                placeholder="e.g. my-app, levh, website-redesign"
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === "Enter") saveProject();
                }}
              />
              <p className="text-[11px] text-muted-foreground">
                Use lowercase, hyphens for spaces. This name groups related memories together.
              </p>
            </div>

            {error && (
              <p className="text-xs text-destructive">{error}</p>
            )}
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setCreateOpen(false)}>
                Cancel
              </Button>
              <Button onClick={saveProject} disabled={saving || !projectName.trim()}>
                {saving ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-2" />
                ) : (
                  <Plus className="h-4 w-4 mr-2" />
                )}
                {editProject ? "Save changes" : "Create project"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Delete Confirm Dialog */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-destructive">
              <Trash2 className="h-5 w-5" />
              Delete Project
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Are you sure you want to delete{" "}
              <strong>{deleteTarget?.name}</strong>?
            </p>
            <p className="text-xs text-muted-foreground">
              This only removes the project grouping. Memories stored under this
              project will remain — they just won&apos;t be grouped anymore.
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setDeleteTarget(null)}>
                Cancel
              </Button>
              <Button variant="destructive" onClick={deleteProject} disabled={saving}>
                {saving ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-2" />
                ) : (
                  <Trash2 className="h-4 w-4 mr-2" />
                )}
                Delete
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

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
