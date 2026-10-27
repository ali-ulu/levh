"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
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
import { toast } from "@/components/ui/toast";
import { MemoryDetailDrawer } from "@/components/memory-detail-drawer";
import { BulkActionBar } from "@/components/bulk-action-bar";
import { VirtualMemoryList } from "@/app/memories/virtual-memory-list";
import { downloadJson, downloadCsv, memoriesToCsv } from "@/lib/download";
import type { Memory, Project, Source } from "@/types";
import {
  Loader2,
  Pin,
  Search,
  Tag,
} from "lucide-react";

const ALL = "__all__";

export default function MemoriesPage() {
  const t = useT();
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

  // Multi-select for bulk actions (#562). `anchor` is the last clicked index,
  // so Shift+click can select the range between it and the new click.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const anchor = useRef<number | null>(null);

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
    } catch (e) {
      toast.error(t("app.memories.loadError"), {
        description: e instanceof Error ? e.message : undefined,
      });
    }
    setLoading(false);
  }, [q, typeFilter, projectFilter, sourceFilter, sessionFilter, pinnedOnly, t]);

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
      toast.success(
        m.pinned ? t("app.memories.toast.unpinned") : t("app.memories.toast.pinned"),
      );
      load();
    } catch (e) {
      toast.error(t("app.memories.toast.pinFailed"), {
        description: e instanceof Error ? e.message : undefined,
      });
    }
  };

  const remove = async (m: Memory) => {
    if (!confirm(t("app.memories.deleteConfirm", { preview: m.content.slice(0, 80) }))) return;
    try {
      await api.deleteMemory(m.id);
      toast.success(t("app.memories.toast.deleted"));
      load();
    } catch (e) {
      toast.error(t("app.memories.toast.deleteFailed"), {
        description: e instanceof Error ? e.message : undefined,
      });
    }
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
      toast.success(t("app.memories.toast.saved"));
      load();
    } catch (e) {
      toast.error(t("app.memories.toast.saveFailed"), {
        description: e instanceof Error ? e.message : undefined,
      });
    }
    setSaving(false);
  };

  // ── Multi-select + bulk actions (#562) ────────────────────────────
  const toggleSelect = (m: Memory, index: number, shiftKey: boolean) => {
    // Resolve the range before touching state: a `setState` updater runs during
    // the next render, by which point `anchor.current` has already advanced to
    // this click and the range would collapse to a single row.
    const from = anchor.current;
    const range =
      shiftKey && from !== null
        ? displayed.slice(Math.min(from, index), Math.max(from, index) + 1)
        : [m];
    const shouldSelect = !selectedIds.has(m.id);
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const item of range) {
        if (shouldSelect) next.add(item.id);
        else next.delete(item.id);
      }
      return next;
    });
    anchor.current = index;
  };

  const selectAll = (checked: boolean) => {
    setSelectedIds(checked ? new Set(displayed.map((m) => m.id)) : new Set());
    anchor.current = null;
  };

  // Each id is its own request, so one failure does not silently drop the rest;
  // the toast reports the count that actually succeeded.
  const runBulk = async (verb: string, fn: (m: Memory) => Promise<unknown>) => {
    const targets = displayed.filter((m) => selectedIds.has(m.id));
    if (targets.length === 0 || bulkBusy) return;
    setBulkBusy(true);
    const results = await Promise.allSettled(targets.map(fn));
    const ok = results.filter((r) => r.status === "fulfilled").length;
    const failed = results.length - ok;
    if (failed === 0) {
      toast.success(t("app.memories.bulk.done", { verb, count: ok }));
    } else {
      toast.error(t("app.memories.bulk.partial", { verb, ok, failed }));
    }
    setSelectedIds(new Set());
    setBulkBusy(false);
    load();
  };

  const bulkPin = (pinned: boolean) =>
    runBulk(pinned ? t("app.memories.bulk.verbPin") : t("app.memories.bulk.verbUnpin"), (m) =>
      api.pinMemory(m.id, pinned),
    );

  const bulkDelete = () => {
    const count = selectedIds.size;
    if (!confirm(t("app.memories.bulk.deleteConfirm", { count }))) return;
    runBulk(t("app.memories.bulk.verbDelete"), (m) => api.deleteMemory(m.id));
  };

  const selectedMemories = () => displayed.filter((m) => selectedIds.has(m.id));

  const bulkExportJson = () => {
    const rows = selectedMemories();
    downloadJson(`levh-memories-${rows.length}.json`, rows);
  };

  const bulkExportCsv = () => {
    const rows = selectedMemories();
    downloadCsv(`levh-memories-${rows.length}.csv`, memoriesToCsv(rows));
  };

  // Collect top tags from displayed memories for the tag cloud
  const topTags = allTags.slice(0, 12);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-[-0.02em]">{t("app.memories.title")}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {t("app.memories.subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          {t(
            displayed.length === 1
              ? "app.memories.count.one"
              : "app.memories.count.other",
            { count: displayed.length },
          )}
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
          placeholder={t("app.memories.searchPlaceholder")}
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
          <SelectTrigger className="w-32" aria-label={t("app.memories.filter.typeLabel")}>
            <SelectValue placeholder={t("app.memories.filter.typePlaceholder")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("app.memories.filter.allTypes")}</SelectItem>
            <SelectItem value="episodic">{t("app.memories.filter.episodic")}</SelectItem>
            <SelectItem value="short_term">{t("app.memories.filter.shortTerm")}</SelectItem>
          </SelectContent>
        </Select>
        <Select value={projectFilter} onValueChange={setProjectFilter}>
          <SelectTrigger className="w-36" aria-label={t("app.memories.filter.projectLabel")}>
            <SelectValue placeholder={t("app.memories.filter.projectPlaceholder")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("app.memories.filter.allProjects")}</SelectItem>
            {projects.map((p) => (
              <SelectItem key={p.name} value={p.name}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={sourceFilter} onValueChange={setSourceFilter}>
          <SelectTrigger className="w-36" aria-label={t("app.memories.filter.sourceLabel")}>
            <SelectValue placeholder={t("app.memories.filter.sourcePlaceholder")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("app.memories.filter.allSources")}</SelectItem>
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
          {t("app.memories.filter.pinned")}
        </Button>
        {sessionFilter && (
          <Badge
            variant="secondary"
            className="h-9 px-3 cursor-pointer"
            onClick={() => setSessionFilter("")}
          >
            {t("app.memories.filter.session", { session: sessionFilter.slice(0, 8) })}
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
                  ? t("app.memories.empty.search", { query: q })
                  : t("app.memories.empty.default")}
              </p>
            </div>
          </CardContent>
        </Card>
      ) : (
        <VirtualMemoryList
          memories={displayed}
          selectedIds={selectedIds}
          onToggleSelect={toggleSelect}
          onOpen={setSelectedMemory}
          onPin={togglePin}
          onEdit={openEdit}
          onDelete={remove}
          onTagClick={setSelectedTag}
          onSelectAll={selectAll}
        />
      )}

      <BulkActionBar
        count={selectedIds.size}
        busy={bulkBusy}
        onPin={() => bulkPin(true)}
        onUnpin={() => bulkPin(false)}
        onDelete={bulkDelete}
        onExportJson={bulkExportJson}
        onExportCsv={bulkExportCsv}
        onClear={() => setSelectedIds(new Set())}
      />

      {/* Edit dialog */}
      <Dialog open={!!editMemory} onOpenChange={(open) => !open && setEditMemory(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{t("app.memories.edit.title")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label className="text-xs">{t("app.memories.edit.content")}</Label>
              <Textarea
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                rows={4}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label className="text-xs">{t("app.memories.edit.tags")}</Label>
                <Input value={editTags} onChange={(e) => setEditTags(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">{t("app.memories.edit.project")}</Label>
                <Input value={editProject} onChange={(e) => setEditProject(e.target.value)} />
              </div>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">{t("app.memories.edit.importance", { value: editImportance.toFixed(1) })}</Label>
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
                {t("app.memories.edit.cancel")}
              </Button>
              <Button onClick={saveEdit} disabled={!editContent.trim() || saving}>
                {saving && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                {t("app.memories.edit.save")}
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
