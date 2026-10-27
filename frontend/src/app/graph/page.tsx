"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MemoryDetailDrawer } from "@/components/memory-detail-drawer";
import { EntityRelationshipMap } from "@/app/graph/entity-relationship-map";
import { BulkActionBar } from "@/components/bulk-action-bar";
import { downloadJson, downloadCsv, toCsv } from "@/lib/download";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import type { EntityRow, Memory } from "@/types";
import {
  ArrowLeft,
  Building2,
  CalendarDays,
  CheckSquare,
  FileText,
  Loader2,
  Network,
  RefreshCw,
  Search,
  User,
} from "lucide-react";

const TYPE_ICON: Record<string, typeof User> = {
  person: User,
  organization: Building2,
  event: CalendarDays,
  document: FileText,
  task: CheckSquare,
};

const TYPES = ["person", "organization", "event", "document", "task"] as const;

// Consistent small palette for entity types, reused by the mini relationship
// graph node fills.
const TYPE_HEX: Record<string, string> = {
  person: "#2a78d6",
  organization: "#1baf7a",
  event: "#eda100",
  document: "#4a3aa7",
  task: "#e34948",
};

function typeHex(t: string): string {
  return TYPE_HEX[t] ?? "#6b7280";
}

function typeLabel(t: string): string {
  return t.charAt(0).toUpperCase() + t.slice(1);
}

interface RelatedEntity {
  id: string;
  type: string;
  name: string;
  shared: number;
}

export default function GraphPage() {
  const t = useT();
  const [entities, setEntities] = useState<EntityRow[]>([]);
  const [byType, setByType] = useState<Record<string, number>>({});
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [reindexing, setReindexing] = useState(false);

  const [selected, setSelected] = useState<EntityRow | null>(null);
  const [related, setRelated] = useState<RelatedEntity[]>([]);
  const [entityMemories, setEntityMemories] = useState<Memory[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [drawerMemory, setDrawerMemory] = useState<Memory | null>(null);

  const load = useCallback(async (type: string, query: string) => {
    setLoading(true);
    try {
      const [statsRes, entitiesRes] = await Promise.all([
        api.entityStats(),
        query.trim()
          ? api.searchEntities(query.trim(), type)
          : api.listEntities(type),
      ]);
      setByType(statsRes.by_type);
      setEntities(entitiesRes.entities);
    } catch {}
    setLoading(false);
  }, []);

  // Deep link: /graph/?q=...&type=... survives a reload and can be shared.
  // Read once on mount (the static export has no server-side searchParams),
  // then keep the URL in step as the user filters.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const q = params.get("q");
    const type = params.get("type");
    if (q) setSearch(q);
    if (type) setTypeFilter(type);
  }, []);

  // Debounce the query so a fast typist issues one request, not one per key.
  useEffect(() => {
    const timer = setTimeout(() => load(typeFilter, search), 300);
    return () => clearTimeout(timer);
  }, [load, typeFilter, search]);

  // Reflect the current filters into the URL without a navigation, so a
  // deep-linked page matches what is on screen without re-running the effect.
  useEffect(() => {
    const params = new URLSearchParams();
    if (search.trim()) params.set("q", search.trim());
    if (typeFilter) params.set("type", typeFilter);
    const qs = params.toString();
    window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
  }, [search, typeFilter]);

  const reindex = async () => {
    setReindexing(true);
    try {
      await api.reindexEntities();
      await load(typeFilter, search);
    } catch {}
    setReindexing(false);
  };

  const openEntity = async (e: EntityRow) => {
    setSelected(e);
    setDetailLoading(true);
    try {
      const result = await api.getEntity(e.id);
      setEntityMemories(result.memories);
      setRelated(result.related);
    } catch {
      setEntityMemories([]);
      setRelated([]);
    }
    setDetailLoading(false);
  };

  // Used by the relationship map — a satellite node only carries
  // {id, type, name, shared}, not a full EntityRow, so re-fetch by id and
  // use the returned `entity` as the new selection.
  const openEntityById = async (id: string) => {
    setDetailLoading(true);
    try {
      const result = await api.getEntity(id);
      setSelected(result.entity);
      setEntityMemories(result.memories);
      setRelated(result.related);
    } catch {
      setEntityMemories([]);
      setRelated([]);
    }
    setDetailLoading(false);
  };

  // Multi-depth expansion for the relationship map (#559): fetch a node's
  // neighbors without changing the selection, so the map can grow two or three
  // hops while the detail panel stays on the entity the user opened.
  const expandNeighbors = async (id: string): Promise<RelatedEntity[]> => {
    const result = await api.getEntity(id);
    return result.related;
  };

  const totalEntities = Object.values(byType).reduce((a, b) => a + b, 0);

  // ── Multi-select + export (#562). The entity index is read-only server-side
  // (no pin/delete endpoints), so the bulk bar offers export alone here. ──
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const anchor = useRef<number | null>(null);

  const toggleSelect = (e: EntityRow, index: number, shiftKey: boolean) => {
    const from = anchor.current;
    const range =
      shiftKey && from !== null
        ? entities.slice(Math.min(from, index), Math.max(from, index) + 1)
        : [e];
    const shouldSelect = !selectedIds.has(e.id);
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

  const selectAllEntities = (checked: boolean) => {
    setSelectedIds(checked ? new Set(entities.map((e) => e.id)) : new Set());
    anchor.current = null;
  };

  const selectedEntities = () => entities.filter((e) => selectedIds.has(e.id));

  const exportEntities = (format: "json" | "csv") => {
    const rows = selectedEntities();
    const filename = `levh-entities-${rows.length}.${format}`;
    if (format === "json") {
      downloadJson(filename, rows);
    } else {
      downloadCsv(
        filename,
        toCsv(
          ["id", "name", "type", "mentions", "updated_at"],
          rows.map((r) => [r.id, r.name, r.type, r.mentions, r.updated_at ?? ""]),
        ),
      );
    }
  };

  // ── Detail view ──────────────────────────────────────────────────
  if (selected) {
    const Icon = TYPE_ICON[selected.type] ?? Network;
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => setSelected(null)}>
          <ArrowLeft className="h-4 w-4 mr-1.5" />
          {t("app.graph.allEntities")}
        </Button>

        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3">
            <Icon className="h-6 w-6 text-muted-foreground" />
            <div>
              <h1 className="text-2xl font-bold">{selected.name}</h1>
              <p className="text-sm text-muted-foreground mt-0.5">
                <Badge variant="secondary" className="text-[11px] mr-1.5">
                  {typeLabel(selected.type)}
                </Badge>
                {t(
                  selected.mentions === 1 ? "app.graph.mentions.one" : "app.graph.mentions.other",
                  { count: selected.mentions },
                )}
                {selected.updated_at && (
                  <> {t("app.graph.updated", { date: selected.updated_at.slice(0, 10) })}</>
                )}
              </p>
            </div>
          </div>
        </div>

        {detailLoading ? (
          <div className="flex justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <>
            {related.length > 0 && (
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">{t("app.graph.related.title")}</CardTitle>
                  <p className="text-xs text-muted-foreground">
                    {t("app.graph.related.help")}
                  </p>
                </CardHeader>
                <CardContent className="space-y-4">
                  <EntityRelationshipMap
                    center={selected}
                    related={related}
                    expand={expandNeighbors}
                    onSelect={(id) => openEntityById(id)}
                    typeHex={typeHex}
                    ariaLabel={t("app.graph.relationshipMap", { name: selected.name })}
                  />
                  <div className="flex flex-wrap gap-2">
                    {related.map((r) => (
                      <Badge key={r.id} variant="outline" className="text-xs font-normal">
                        {t("app.graph.related.item", {
                          type: typeLabel(r.type),
                          name: r.name,
                          shared: r.shared,
                        })}
                      </Badge>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}

            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">
                  {t("app.graph.memoriesMentioning", { name: selected.name })}
                </CardTitle>
              </CardHeader>
              <CardContent>
                {entityMemories.length === 0 ? (
                  <p className="text-sm text-muted-foreground py-4 text-center">{t("app.graph.noMemories")}</p>
                ) : (
                  <div className="space-y-2">
                    {entityMemories.map((m) => (
                      <button
                        key={m.id}
                        onClick={() => setDrawerMemory(m)}
                        className="w-full text-left border rounded-lg p-3 hover:bg-accent/40 transition-colors"
                      >
                        <p className="text-sm line-clamp-2">{m.content}</p>
                        <div className="flex items-center gap-1.5 mt-1.5">
                          {m.source && (
                            <Badge variant="outline" className="text-[11px]">
                              {m.source.replace("connector:", "")}
                            </Badge>
                          )}
                          <span className="text-[11px] text-muted-foreground">
                            {(m.created_at || "").slice(0, 10)}
                          </span>
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </>
        )}

        {drawerMemory && (
          <MemoryDetailDrawer memory={drawerMemory} onClose={() => setDrawerMemory(null)} />
        )}
      </div>
    );
  }

  // ── List view ────────────────────────────────────────────────────
  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold">{t("app.graph.title")}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {t("app.graph.subtitle")}
          </p>
        </div>
        <Button onClick={reindex} disabled={reindexing}>
          {reindexing ? (
            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4 mr-2" />
          )}
          {t("app.graph.reindex")}
        </Button>
      </div>

      <div className="relative">
        <Search
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
        <Input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          aria-label={t("app.graph.searchLabel")}
          placeholder={t("app.graph.searchPlaceholder")}
          className="pl-9"
        />
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => setTypeFilter("")}
          className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
            typeFilter === "" ? "bg-primary text-primary-foreground" : "hover:bg-accent"
          }`}
        >
          {t("app.graph.all", { count: totalEntities })}
        </button>
        {TYPES.map((t) => (
          <button
            key={t}
            onClick={() => setTypeFilter(t)}
            className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
              typeFilter === t ? "bg-primary text-primary-foreground" : "hover:bg-accent"
            }`}
          >
            {typeLabel(t)} ({byType[t] ?? 0})
          </button>
        ))}
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : entities.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground space-y-2">
            <Network className="h-8 w-8 mx-auto opacity-40" />
            {search.trim() ? (
              <p>{t("app.graph.noMatch", { query: search.trim() })}</p>
            ) : (
              <>
                <p>{t("app.graph.empty.title")}</p>
                <p>{t("app.graph.empty.hint")}</p>
              </>
            )}
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="flex items-center gap-2 px-1">
            <input
              id="entities-select-all"
              type="checkbox"
              className="h-4 w-4 accent-primary"
              checked={entities.length > 0 && entities.every((e) => selectedIds.has(e.id))}
              onChange={(ev) => selectAllEntities(ev.target.checked)}
            />
            <label htmlFor="entities-select-all" className="text-xs text-muted-foreground">
              {t("bulk.selectAll")}
            </label>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {entities.map((e, index) => {
              const Icon = TYPE_ICON[e.type] ?? Network;
              const checked = selectedIds.has(e.id);
              return (
                <div
                  key={e.id}
                  className={cn(
                    "relative border rounded-lg p-3 bg-card transition-colors",
                    checked ? "ring-1 ring-primary" : "hover:bg-accent/40",
                  )}
                >
                  <input
                    type="checkbox"
                    className="absolute right-2 top-2 h-4 w-4 accent-primary"
                    checked={checked}
                    aria-label={t("bulk.selectRow", { preview: e.name })}
                    onChange={() => {}}
                    onClick={(ev) => toggleSelect(e, index, ev.shiftKey)}
                  />
                  <button onClick={() => openEntity(e)} className="w-full text-left">
                    <div className="flex items-center justify-between gap-2 pr-6">
                      <span className="flex items-center gap-1.5 font-medium text-sm truncate">
                        <Icon className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                        {e.name}
                      </span>
                      <Badge variant="secondary" className="text-[11px] shrink-0">
                        {e.mentions}
                      </Badge>
                    </div>
                    <div className="flex items-center justify-between mt-2">
                      <Badge variant="outline" className="text-[11px]">
                        {typeLabel(e.type)}
                      </Badge>
                      {e.updated_at && (
                        <span className="text-[11px] text-muted-foreground">
                          {e.updated_at.slice(0, 10)}
                        </span>
                      )}
                    </div>
                  </button>
                </div>
              );
            })}
          </div>
        </>
      )}

      <BulkActionBar
        count={selectedIds.size}
        onExportJson={() => exportEntities("json")}
        onExportCsv={() => exportEntities("csv")}
        onClear={() => setSelectedIds(new Set())}
      />
    </div>
  );
}
