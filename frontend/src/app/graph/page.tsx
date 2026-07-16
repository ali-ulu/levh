"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MemoryDetailDrawer } from "@/components/memory-detail-drawer";
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

function truncateLabel(s: string, max = 12): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

interface RelatedEntity {
  id: string;
  type: string;
  name: string;
  shared: number;
}

/** Small inline-SVG relationship map: center = selected entity, satellites =
 * up to 8 related entities on a circle, connected by lines whose
 * thickness/opacity scale with the `shared` co-occurrence count. */
function RelationshipMiniGraph({
  center,
  related,
  onSelect,
}: {
  center: EntityRow;
  related: RelatedEntity[];
  onSelect?: (id: string) => void;
}) {
  const satellites = related.slice(0, 8);
  const size = 280;
  const cx = size / 2;
  const cy = size / 2;
  const radius = 100;
  const maxShared = Math.max(1, ...satellites.map((r) => r.shared));

  return (
    <svg
      viewBox={`0 0 ${size} ${size}`}
      width="100%"
      style={{ maxHeight: 280 }}
      role="img"
      aria-label={`Relationship map for ${center.name}`}
    >
      {satellites.map((r, i) => {
        const angle = (2 * Math.PI * i) / satellites.length - Math.PI / 2;
        const x = cx + radius * Math.cos(angle);
        const y = cy + radius * Math.sin(angle);
        const weight = r.shared / maxShared;
        return (
          <line
            key={`edge-${r.id}`}
            x1={cx}
            y1={cy}
            x2={x}
            y2={y}
            stroke="currentColor"
            strokeOpacity={0.15 + weight * 0.45}
            strokeWidth={1 + weight * 3}
            className="text-muted-foreground"
          />
        );
      })}

      {satellites.map((r, i) => {
        const angle = (2 * Math.PI * i) / satellites.length - Math.PI / 2;
        const x = cx + radius * Math.cos(angle);
        const y = cy + radius * Math.sin(angle);
        return (
          <g
            key={`node-${r.id}`}
            transform={`translate(${x}, ${y})`}
            onClick={() => onSelect?.(r.id)}
            style={{ cursor: onSelect ? "pointer" : "default" }}
          >
            <circle r={14} fill={typeHex(r.type)} fillOpacity={0.85} stroke="white" strokeWidth={1.5} />
            <text
              y={26}
              textAnchor="middle"
              fontSize={10}
              className="fill-foreground"
            >
              {truncateLabel(r.name)}
            </text>
          </g>
        );
      })}

      {/* Center node drawn last so it sits above edge lines */}
      <g transform={`translate(${cx}, ${cy})`}>
        <circle r={20} fill={typeHex(center.type)} stroke="white" strokeWidth={2} />
        <text y={34} textAnchor="middle" fontSize={11} fontWeight={600} className="fill-foreground">
          {truncateLabel(center.name, 16)}
        </text>
      </g>
    </svg>
  );
}

export default function GraphPage() {
  const [entities, setEntities] = useState<EntityRow[]>([]);
  const [byType, setByType] = useState<Record<string, number>>({});
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [reindexing, setReindexing] = useState(false);

  const [selected, setSelected] = useState<EntityRow | null>(null);
  const [related, setRelated] = useState<RelatedEntity[]>([]);
  const [entityMemories, setEntityMemories] = useState<Memory[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [drawerMemory, setDrawerMemory] = useState<Memory | null>(null);

  const load = useCallback(async (type: string) => {
    setLoading(true);
    try {
      const [statsRes, entitiesRes] = await Promise.all([
        api.entityStats(),
        api.listEntities(type),
      ]);
      setByType(statsRes.by_type);
      setEntities(entitiesRes.entities);
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => {
    load(typeFilter);
  }, [load, typeFilter]);

  const reindex = async () => {
    setReindexing(true);
    try {
      await api.reindexEntities();
      await load(typeFilter);
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

  // Used by the mini relationship graph — a satellite node only carries
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

  const totalEntities = Object.values(byType).reduce((a, b) => a + b, 0);

  // ── Detail view ──────────────────────────────────────────────────
  if (selected) {
    const Icon = TYPE_ICON[selected.type] ?? Network;
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => setSelected(null)}>
          <ArrowLeft className="h-4 w-4 mr-1.5" />
          All entities
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
                {selected.mentions} mentions
                {selected.updated_at && <> · updated {selected.updated_at.slice(0, 10)}</>}
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
                  <CardTitle className="text-base">Related entities</CardTitle>
                  <p className="text-xs text-muted-foreground">
                    Line thickness scales with how often each pair co-occurs in the same memory.
                  </p>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex justify-center">
                    <div className="w-full max-w-xs">
                      <RelationshipMiniGraph
                        center={selected}
                        related={related}
                        onSelect={(id) => openEntityById(id)}
                      />
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {related.map((r) => (
                      <Badge key={r.id} variant="outline" className="text-xs font-normal">
                        [{typeLabel(r.type)}] {r.name} · shared {r.shared}
                      </Badge>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}

            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">
                  Memories mentioning {selected.name}
                </CardTitle>
              </CardHeader>
              <CardContent>
                {entityMemories.length === 0 ? (
                  <p className="text-sm text-muted-foreground py-4 text-center">No memories.</p>
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
          <h1 className="text-2xl font-bold">Graph</h1>
          <p className="text-sm text-muted-foreground mt-1">
            The persistent entity knowledge graph — people, organizations,
            events, documents, and tasks extracted from your memories, and how
            they connect.
          </p>
        </div>
        <Button onClick={reindex} disabled={reindexing}>
          {reindexing ? (
            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4 mr-2" />
          )}
          Reindex
        </Button>
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => setTypeFilter("")}
          className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
            typeFilter === "" ? "bg-primary text-primary-foreground" : "hover:bg-accent"
          }`}
        >
          All ({totalEntities})
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
            <p>No entities yet.</p>
            <p>Click Reindex to build the entity graph from your captured memories.</p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {entities.map((e) => {
            const Icon = TYPE_ICON[e.type] ?? Network;
            return (
              <button
                key={e.id}
                onClick={() => openEntity(e)}
                className="text-left border rounded-lg p-3 bg-card hover:bg-accent/40 transition-colors"
              >
                <div className="flex items-center justify-between gap-2">
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
            );
          })}
        </div>
      )}
    </div>
  );
}
