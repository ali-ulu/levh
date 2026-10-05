"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
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
  const t = useT();
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
      setError(t("app.projects.error.nameRequired"));
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
      setError(e instanceof Error ? e.message : t("app.projects.error.saveFailed"));
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
      setCtxContent(t("app.projects.error.generateFailed", { error: e instanceof Error ? e.message : String(e) }));
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
          <h1 className="text-2xl font-bold">{t("app.projects.title")}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {t("app.projects.subtitle")}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => openDialog(ALL_PROJECTS)}>
            <FileText className="h-4 w-4 mr-2" />
            {t("app.projects.contextAll")}
          </Button>
          <Button onClick={openCreate}>
            <Plus className="h-4 w-4 mr-2" />
            {t("app.projects.newProject")}
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
              <p className="text-base font-medium text-foreground">{t("app.projects.empty.title")}</p>
              <p className="max-w-md mx-auto">
                {t("app.projects.empty.prefix")}{" "}
                <code className="text-xs bg-muted px-1 rounded">store_memory</code>{" "}
                {t("app.projects.empty.suffix")}
              </p>
            </div>
            <Button onClick={openCreate} className="mt-4">
              <Sparkles className="h-4 w-4 mr-2" />
              {t("app.projects.empty.action")}
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
                      title={t("app.projects.card.editTitle")}
                    >
                      <Edit3 className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={() => setDeleteTarget(p)}
                      className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive"
                      title={t("app.projects.card.deleteTitle")}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="text-sm text-muted-foreground">
                  {t(
                    p.memory_count === 1
                      ? "app.projects.memoryCount.one"
                      : "app.projects.memoryCount.other",
                    { count: p.memory_count },
                  )}
                  {p.last_used && (
                    <> · {t("app.projects.lastUsed", { date: new Date(p.last_used).toLocaleDateString("en-GB") })}</>
                  )}
                </div>
                <div className="flex gap-2">
                  <Button asChild variant="outline" size="sm">
                    <Link href={`/memories/?project=${encodeURIComponent(p.name)}`}>
                      {t("app.projects.browse")}
                    </Link>
                  </Button>
                  <Button size="sm" onClick={() => openDialog(p.name)}>
                    <FileText className="h-3.5 w-3.5 mr-1.5" />
                    {t("app.projects.contextFile")}
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
              {editProject
                ? t("app.projects.dialog.editTitle", { name: editProject.name })
                : t("app.projects.dialog.newTitle")}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label className="text-xs">{t("app.projects.dialog.nameLabel")}</Label>
              <Input
                value={projectName}
                onChange={(e) => setProjectName(e.target.value)}
                placeholder={t("app.projects.dialog.namePlaceholder")}
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === "Enter") saveProject();
                }}
              />
              <p className="text-[11px] text-muted-foreground">
                {t("app.projects.dialog.nameHelp")}
              </p>
            </div>

            {error && (
              <p className="text-xs text-destructive">{error}</p>
            )}
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setCreateOpen(false)}>
                {t("app.projects.cancel")}
              </Button>
              <Button onClick={saveProject} disabled={saving || !projectName.trim()}>
                {saving ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-2" />
                ) : (
                  <Plus className="h-4 w-4 mr-2" />
                )}
                {editProject ? t("app.projects.dialog.saveChanges") : t("app.projects.dialog.create")}
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
              {t("app.projects.delete.title")}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              {t("app.projects.delete.promptPrefix")}{" "}
              <strong>{deleteTarget?.name}</strong>?
            </p>
            <p className="text-xs text-muted-foreground">
              {t("app.projects.delete.help")}
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setDeleteTarget(null)}>
                {t("app.projects.cancel")}
              </Button>
              <Button variant="destructive" onClick={deleteProject} disabled={saving}>
                {saving ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-2" />
                ) : (
                  <Trash2 className="h-4 w-4 mr-2" />
                )}
                {t("app.projects.delete.action")}
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
              {ctxProject === ALL_PROJECTS
                ? t("app.projects.context.titleAll")
                : t("app.projects.context.titleProject", { name: ctxProject })}
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
                <SelectTrigger className="w-44" aria-label={t("app.projects.context.formatAria")}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="claude">CLAUDE.md</SelectItem>
                  <SelectItem value="cursor">.cursorrules</SelectItem>
                </SelectContent>
              </Select>
              <Button variant="outline" size="sm" onClick={copy} disabled={generating || !ctxContent}>
                {copied ? <Check className="h-3.5 w-3.5 mr-1.5" /> : <Copy className="h-3.5 w-3.5 mr-1.5" />}
                {copied ? t("app.projects.context.copied") : t("app.projects.context.copy")}
              </Button>
              <Button variant="outline" size="sm" onClick={download} disabled={generating || !ctxContent}>
                <Download className="h-3.5 w-3.5 mr-1.5" />
                {t("app.projects.context.download", { filename: ctxFilename })}
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
              {t("app.projects.context.helpPrefix")}{" "}
              <code className="bg-muted px-1 rounded">levh context -o CLAUDE.md</code>.
            </p>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
