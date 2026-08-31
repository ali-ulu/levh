"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
import { Textarea } from "@/components/ui/textarea";
import { MemoryDetailDrawer } from "@/components/memory-detail-drawer";
import type { Memory, Project, Source } from "@/types";
import {
  Clock,
  Eye,
  FolderGit2,
  Loader2,
  Pencil,
  Pin,
  PinOff,
  Search,
  Sparkles,
  Tag,
  Trash2,
} from "lucide-react";

const ALL = "__all__";

function relativeTime(dateStr: string) {
  const time = new Date(dateStr).getTime();
  if (!Number.isFinite(time)) return "";
  const delta = Math.max(0, Date.now() - time);
  const minutes = Math.floor(delta / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

export default function MemoriesPage() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [projects, setProjects] = useState<Project[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [allTags, setAllTags] = useState<string[]>([]);

  // Filters
  const [q, setQ] = useState("");
  const [typeFilter, setTypeFilter] = useState(ALL);
  const [projectFilter, setProjectFilter] = useState(ALL);
  const [sourceFilter, setSourceFilter] = useState(ALL);
  const [sessionFilter, setSessionFilter] = useState("");
  const [pinnedOnly, setPinnedOnly] = useState(false);
  const [selectedTag, setSelectedTag] = useState<string | null>(null);

  // Deep links: /memories/?project=X or /memories/?session=Y
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const project = params.get("project");
    const session = params.get("session");
    const query = params.get("q");
    const tag = params.get("tag");
    if (project) setProjectFilter(project);
    if (session) setSessionFilter(session);
    if (query) setQ(query);
    if (tag) setSelectedTag(tag);
  }, []);

  const [selectedMemory, setSelectedMemory] = useState<Memory | null>(null);

  // Edit dialog
  const [editMemory, setEditMemory] = useState<Memory | null>(null);
  const [editContent, setEditContent] = useState("");
  const [editImportance, setEditImportance] = useState(0.5);
  const [editTags, setEditTags] = useState("");
  const [editProject, setEditProject] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [mems, projs, srcs, tags] = await Promise.all([
        api.listMemories({
          q: q.trim() || undefined,
          memory_type: typeFilter === ALL ? undefined : typeFilter,
          project: projectFilter === ALL ? undefined : projectFilter,
          source: sourceFilter === ALL ? undefined : sourceFilter,
          session_id: sessionFilter || undefined,
          pinned: pinnedOnly ? "true" : undefined,
          limit: 200,
        }),
        api.listProjects(),
        api.listSources(),
        api.listTags(),
      ]);
      setMemories(mems);
      setProjects(projs.projects);
      setSources(srcs.sources);
      setAllTags(tags.tags.map((t) => t.name));
    } catch {}
    setLoading(false);
  }, [q, typeFilter, projectFilter, sourceFilter, sessionFilter, pinnedOnly]);

  useEffect(() => {
    const t = setTimeout(load, q ? 300 : 0);
    return () => clearTimeout(t);
  }, [load, q]);

  // Filter by tag client-side if tag selected
  const displayed = selectedTag
    ? memories.filter((m) => m.tags.includes(selectedTag))
    : memories;

  const togglePin = async (m: Memory) => {
    try {
      await api.pinMemory(m.id, !m.pinned);
      load();
    } catch {}
  };

  const remove = async (m: Memory) => {
    if (!confirm(`Delete this memory permanently?\n\n"${m.content.slice(0, 80)}..."`)) return;
    try {
      await api.deleteMemory(m.id);
      load();
    } catch {}
  };

  const openEdit = (m: Memory) => {
    setEditMemory(m);
    setEditContent(m.content);
    setEditImportance(m.importance);
    setEditTags(m.tags.join(", "));
    setEditProject(m.project ?? "");
  };

  const saveEdit = async () => {
    if (!editMemory || saving) return;
    setSaving(true);
    try {
      await api.updateMemory(editMemory.id, {
        content: editContent,
        importance: editImportance,
        tags: editTags.split(",").map((t) => t.trim()).filter(Boolean),
        project: editProject.trim() || undefined,
      });
      setEditMemory(null);
      load();
    } catch {}
    setSaving(false);
  };

  // Collect top tags from displayed memories for the tag cloud
  const topTags = allTags.slice(0, 12);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-[-0.02em]">Memories</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Browse, search, and manage everything your AI remembers.
          </p>
        </div>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span className="tabular-nums">{displayed.length}</span> memories
          {selectedTag && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1"
              onClick={() => setSelectedTag(null)}
            >
              <Tag className="h-3 w-3" />
              {selectedTag} ×
            </Button>
          )}
        </div>
      </div>

      {/* Search bar */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          className="pl-10 h-11 text-sm"
          placeholder="Search memories by content..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>

      {/* Tag cloud */}
      {topTags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {topTags.map((tag) => (
            <button
              key={tag}
              onClick={() => setSelectedTag(selectedTag === tag ? null : tag)}
              className={`tag-chip ${selectedTag === tag ? "active" : ""}`}
            >
              <Tag className="h-2.5 w-2.5" />
              {tag}
            </button>
          ))}
        </div>
      )}

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-2">
        <Select value={typeFilter} onValueChange={setTypeFilter}>
          <SelectTrigger className="w-32">
            <SelectValue placeholder="Type" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All types</SelectItem>
            <SelectItem value="episodic">Episodic</SelectItem>
            <SelectItem value="short_term">Short-term</SelectItem>
          </SelectContent>
        </Select>
        <Select value={projectFilter} onValueChange={setProjectFilter}>
          <SelectTrigger className="w-36">
            <SelectValue placeholder="Project" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All projects</SelectItem>
            {projects.map((p) => (
              <SelectItem key={p.name} value={p.name}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={sourceFilter} onValueChange={setSourceFilter}>
          <SelectTrigger className="w-36">
            <SelectValue placeholder="Source" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All sources</SelectItem>
            {sources.map((s) => (
              <SelectItem key={s.name} value={s.name}>
                {s.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          variant={pinnedOnly ? "default" : "outline"}
          size="sm"
          className="h-9"
          onClick={() => setPinnedOnly(!pinnedOnly)}
        >
          <Pin className="h-3.5 w-3.5 mr-1.5" />
          Pinned
        </Button>
        {sessionFilter && (
          <Badge
            variant="secondary"
            className="h-9 px-3 cursor-pointer"
            onClick={() => setSessionFilter("")}
          >
            session: {sessionFilter.slice(0, 8)}… ✕
          </Badge>
        )}
      </div>

      {/* Results */}
      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="skeleton-card h-20" />
          ))}
        </div>
      ) : displayed.length === 0 ? (
        <Card>
          <CardContent className="py-16 text-center">
            <div className="flex flex-col items-center gap-3">
              <div className="h-12 w-12 rounded-full bg-muted flex items-center justify-center">
                <Search className="h-5 w-5 text-muted-foreground" />
              </div>
              <p className="text-sm text-muted-foreground">
                {q
                  ? `No memories match "${q}"`
                  : "No memories yet. Add your first memory to get started."}
              </p>
            </div>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {displayed.map((m) => (
            <div
              key={m.id}
              className={`memory-card group ${m.pinned ? "pinned" : ""}`}
            >
              <div className="flex items-start gap-3">
                {/* Memory type indicator */}
                <div
                  className={`memory-type-indicator ${
                    m.memory_type === "episodic" ? "episodic" : "short-term"
                  }`}
                />

                {/* Content */}
                <div className="flex-1 min-w-0">
                  <p className="text-sm leading-relaxed line-clamp-2">
                    {m.content}
                  </p>
                  <div className="flex flex-wrap items-center gap-1.5 mt-2">
                    {m.pinned && (
                      <Badge
                        variant="secondary"
                        className="text-[11px] bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20"
                      >
                        <Pin className="h-2.5 w-2.5 mr-1" />
                        pinned
                      </Badge>
                    )}
                    {m.project && (
                      <Badge variant="outline" className="text-[11px]">
                        <FolderGit2 className="h-2.5 w-2.5 mr-1" />
                        {m.project}
                      </Badge>
                    )}
                    {m.source && (
                      <Badge variant="outline" className="text-[11px]">
                        {m.source}
                      </Badge>
                    )}
                    {m.tags.slice(0, 4).map((t) => (
                      <button
                        key={t}
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedTag(t);
                        }}
                        className="inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded border border-dashed border-muted-foreground/30 text-muted-foreground hover:border-primary/50 hover:text-primary transition-colors"
                      >
                        {t}
                      </button>
                    ))}
                    {m.tags.length > 4 && (
                      <span className="text-[10px] text-muted-foreground/60">
                        +{m.tags.length - 4}
                      </span>
                    )}
                  </div>
                </div>

                {/* Meta + actions */}
                <div className="flex flex-col items-end gap-1 shrink-0">
                  <span className="flex items-center gap-1 text-[11px] text-muted-foreground tabular-nums">
                    <Clock className="h-3 w-3" />
                    {relativeTime(m.created_at)}
                  </span>
                  <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      onClick={() => togglePin(m)}
                      aria-label={m.pinned ? "Unpin" : "Pin"}
                    >
                      {m.pinned ? (
                        <PinOff className="h-3.5 w-3.5" />
                      ) : (
                        <Pin className="h-3.5 w-3.5" />
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      onClick={() => setSelectedMemory(m)}
                      aria-label="View"
                    >
                      <Eye className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      onClick={() => openEdit(m)}
                      aria-label="Edit"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      onClick={() => remove(m)}
                      aria-label="Delete"
                    >
                      <Trash2 className="h-3.5 w-3.5 text-destructive" />
                    </Button>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Edit dialog */}
      <Dialog open={!!editMemory} onOpenChange={(open) => !open && setEditMemory(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Edit Memory</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label className="text-xs">Content</Label>
              <Textarea
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                rows={4}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label className="text-xs">Tags (comma-separated)</Label>
                <Input value={editTags} onChange={(e) => setEditTags(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Project</Label>
                <Input value={editProject} onChange={(e) => setEditProject(e.target.value)} />
              </div>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Importance: {editImportance.toFixed(1)}</Label>
              <input
                type="range"
                min={0}
                max={1}
                step={0.1}
                value={editImportance}
                onChange={(e) => setEditImportance(parseFloat(e.target.value))}
                className="w-full accent-primary"
              />
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setEditMemory(null)}>
                Cancel
              </Button>
              <Button onClick={saveEdit} disabled={!editContent.trim() || saving}>
                {saving && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                Save
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {selectedMemory && (
        <MemoryDetailDrawer
          memory={selectedMemory}
          onClose={() => setSelectedMemory(null)}
          onChanged={load}
          onSelectRelated={setSelectedMemory}
        />
      )}
    </div>
  );
}
