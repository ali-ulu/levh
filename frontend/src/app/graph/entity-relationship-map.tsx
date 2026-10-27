"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Minus, Plus, RotateCcw } from "lucide-react";
import { useT } from "@/lib/i18n";

export interface MapNeighbor {
  id: string;
  type: string;
  name: string;
  shared: number;
}

export interface MapCenter {
  id: string;
  type: string;
  name: string;
  mentions: number;
}

interface Props {
  center: MapCenter;
  related: MapNeighbor[];
  /** Fetch a node's own neighbors, so the map can grow past one hop. Omitted
   * when the page cannot resolve ids (the satellites then stay one hop). */
  expand?: (id: string) => Promise<MapNeighbor[]>;
  onSelect?: (id: string) => void;
  typeHex: (type: string) => string;
  ariaLabel: string;
}

interface GraphNode extends MapNeighbor {
  depth: number;
  parentId: string | null;
}

interface GraphEdge {
  source: string;
  target: string;
  shared: number;
}

const WIDTH = 640;
const HEIGHT = 420;
const PADDING = 44;
// The layout runs to convergence synchronously rather than animating, so the
// result is identical on the server, on first paint and after every re-render
// — and `prefers-reduced-motion` needs nothing special because there is no
// motion to suppress.
const ITERATIONS = 300;

/** Seeded PRNG (mulberry32). `Math.random` would make the layout differ between
 * the prerender and hydration and trip React's mismatch warning; a fixed seed
 * makes the whole graph deterministic (#559). */
function mulberry32(seed: number) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hashSeed(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

interface Point {
  x: number;
  y: number;
}

/** Fruchterman-Reingold, run for a fixed number of iterations. O(n²) per tick,
 * which is fine for a relationship map (the server caps neighbors at 20) and
 * keeps the component dependency-free. */
function forceLayout(ids: string[], edges: GraphEdge[], seed: number): Record<string, Point> {
  const n = ids.length;
  const rand = mulberry32(seed);
  const area = (WIDTH - PADDING * 2) * (HEIGHT - PADDING * 2);
  const k = Math.sqrt(area / Math.max(1, n));
  const pos: Record<string, Point> = {};
  ids.forEach((id) => {
    pos[id] = { x: PADDING + rand() * (WIDTH - PADDING * 2), y: PADDING + rand() * (HEIGHT - PADDING * 2) };
  });
  const index = new Map(ids.map((id, i) => [id, i]));

  let temp = (WIDTH - PADDING * 2) / 10;
  const cooling = temp / (ITERATIONS + 1);
  const disp: Point[] = ids.map(() => ({ x: 0, y: 0 }));

  for (let iter = 0; iter < ITERATIONS; iter += 1) {
    disp.forEach((d) => { d.x = 0; d.y = 0; });

    // Repulsion between every pair.
    for (let i = 0; i < n; i += 1) {
      for (let j = i + 1; j < n; j += 1) {
        const a = pos[ids[i]];
        const b = pos[ids[j]];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let dist = Math.hypot(dx, dy);
        if (dist < 0.01) {
          dx = 0.01 * (i - j);
          dy = 0.01;
          dist = Math.hypot(dx, dy);
        }
        const force = (k * k) / dist;
        const ux = (dx / dist) * force;
        const uy = (dy / dist) * force;
        disp[i].x += ux;
        disp[i].y += uy;
        disp[j].x -= ux;
        disp[j].y -= uy;
      }
    }

    // Attraction along edges.
    for (const edge of edges) {
      const i = index.get(edge.source);
      const j = index.get(edge.target);
      if (i === undefined || j === undefined) continue;
      const a = pos[edge.source];
      const b = pos[edge.target];
      const dist = Math.max(0.01, Math.hypot(a.x - b.x, a.y - b.y));
      const force = (dist * dist) / k;
      const ux = ((a.x - b.x) / dist) * force;
      const uy = ((a.y - b.y) / dist) * force;
      disp[i].x -= ux;
      disp[i].y -= uy;
      disp[j].x += ux;
      disp[j].y += uy;
    }

    for (let i = 0; i < n; i += 1) {
      const d = disp[i];
      const len = Math.max(0.01, Math.hypot(d.x, d.y));
      const step = Math.min(len, temp) / len;
      pos[ids[i]].x += d.x * step;
      pos[ids[i]].y += d.y * step;
      pos[ids[i]].x = Math.min(WIDTH - PADDING, Math.max(PADDING, pos[ids[i]].x));
      pos[ids[i]].y = Math.min(HEIGHT - PADDING, Math.max(PADDING, pos[ids[i]].y));
    }
    temp -= cooling;
  }
  return pos;
}

const nodeRadius = (n: GraphNode, isCenter: boolean) =>
  isCenter ? 22 : 8 + Math.min(10, Math.sqrt(n.shared) * 3);

/** Interactive relationship map: a deterministic force-directed layout with
 * zoom/pan (wheel, drag, touch), keyboard navigation and lazy multi-depth
 * expansion. Extracted from the graph page so the layout, the interaction and
 * the a11y can be tested on their own (#559). */
export function EntityRelationshipMap({ center, related, expand, onSelect, typeHex, ariaLabel }: Props) {
  const t = useT();
  const [extra, setExtra] = useState<Map<string, MapNeighbor[]>>(new Map());
  const [depth, setDepth] = useState(1);
  const [expanding, setExpanding] = useState(false);
  const [view, setView] = useState({ x: 0, y: 0, scale: 1 });
  const [focused, setFocused] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);

  // Build the graph: the center, its direct neighbors, then (when the user asks
  // for more) the neighbors already fetched, breadth-first up to `depth`.
  const { nodes, edges } = useMemo(() => {
    const byId = new Map<string, GraphNode>();
    const edgeList: GraphEdge[] = [];
    const seenEdge = new Set<string>();
    byId.set(center.id, { id: center.id, type: center.type, name: center.name, shared: center.mentions, depth: 0, parentId: null });
    const addEdge = (source: string, target: string, shared: number) => {
      const key = source < target ? `${source}|${target}` : `${target}|${source}`;
      if (seenEdge.has(key)) return;
      seenEdge.add(key);
      edgeList.push({ source, target, shared });
    };
    const neighborsOf = (id: string): MapNeighbor[] =>
      id === center.id ? related : extra.get(id) ?? [];

    let frontier = [center.id];
    for (let d = 1; d <= depth; d += 1) {
      const next: string[] = [];
      for (const parentId of frontier) {
        for (const n of neighborsOf(parentId)) {
          if (!byId.has(n.id)) {
            byId.set(n.id, { ...n, depth: d, parentId });
            next.push(n.id);
          }
          addEdge(parentId, n.id, n.shared);
        }
      }
      frontier = next;
    }
    return { nodes: Array.from(byId.values()), edges: edgeList };
  }, [center, related, extra, depth]);

  const positions = useMemo(
    () => forceLayout(nodes.map((n) => n.id), edges, hashSeed(center.id)),
    [nodes, edges, center.id],
  );

  const expandMore = useCallback(async () => {
    if (!expand || expanding) return;
    setExpanding(true);
    try {
      const targets = nodes.filter((n) => n.depth === depth).map((n) => n.id);
      const fetched = await Promise.all(
        targets.map(async (id) => [id, await expand(id)] as const),
      );
      setExtra((prev) => {
        const next = new Map(prev);
        fetched.forEach(([id, list]) => next.set(id, list));
        return next;
      });
      setDepth((d) => Math.min(3, d + 1));
    } catch {
      // A failed hop leaves the map at its current depth; the control stays
      // available so the user can retry.
    }
    setExpanding(false);
  }, [expand, expanding, nodes, depth]);

  const resetView = useCallback(() => {
    setView({ x: 0, y: 0, scale: 1 });
    setFocused(null);
  }, []);

  // Escape resets the viewport from anywhere in the map.
  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      resetView();
    }
  };

  const moveFocus = (from: string, delta: number) => {
    const ids = nodes.map((n) => n.id);
    const i = ids.indexOf(from);
    if (i < 0) return;
    const next = (i + delta + ids.length) % ids.length;
    setFocused(ids[next]);
    const el = svgRef.current?.querySelector<SVGGElement>(`[data-node-id="${CSS.escape(ids[next])}"]`);
    el?.focus();
  };

  const onWheel = (event: React.WheelEvent) => {
    event.preventDefault();
    setView((v) => {
      const scale = Math.min(4, Math.max(0.4, v.scale * (event.deltaY < 0 ? 1.1 : 0.9)));
      return { ...v, scale };
    });
  };

  const onPointerDown = (event: React.PointerEvent) => {
    (event.target as Element).setPointerCapture?.(event.pointerId);
    drag.current = { x: event.clientX, y: event.clientY, vx: view.x, vy: view.y };
    setDragging(true);
  };
  const onPointerMove = (event: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    setView((v) => ({ ...v, x: d.vx + (event.clientX - d.x), y: d.vy + (event.clientY - d.y) }));
  };
  const onPointerUp = () => { drag.current = null; setDragging(false); };

  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const maxShared = Math.max(1, ...edges.map((e) => e.shared));

  return (
    <div className="space-y-2" onKeyDown={onKeyDown}>
      <div className="flex items-center justify-end gap-1">
        <button
          type="button"
          className="inline-flex h-7 w-7 items-center justify-center rounded-md border hover:bg-accent"
          aria-label={t("app.graph.map.zoomOut")}
          onClick={() => setView((v) => ({ ...v, scale: Math.max(0.4, v.scale * 0.9) }))}
        >
          <Minus className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          className="inline-flex h-7 w-7 items-center justify-center rounded-md border hover:bg-accent"
          aria-label={t("app.graph.map.zoomIn")}
          onClick={() => setView((v) => ({ ...v, scale: Math.min(4, v.scale * 1.1) }))}
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          className="inline-flex h-7 w-7 items-center justify-center rounded-md border hover:bg-accent"
          aria-label={t("app.graph.map.reset")}
          onClick={resetView}
        >
          <RotateCcw className="h-3.5 w-3.5" />
        </button>
        {expand && depth < 3 && (
          <button
            type="button"
            className="h-7 rounded-md border px-2 text-xs hover:bg-accent disabled:opacity-50"
            onClick={expandMore}
            disabled={expanding}
          >
            {expanding ? t("app.graph.map.expanding") : t("app.graph.map.expand", { depth: depth + 1 })}
          </button>
        )}
      </div>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        width="100%"
        role="img"
        aria-label={ariaLabel}
        className="touch-none select-none rounded-lg border bg-card"
        style={{ maxHeight: 420, cursor: dragging ? "grabbing" : "grab" }}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
      >
        <g transform={`translate(${view.x} ${view.y}) scale(${view.scale})`}>
          {edges.map((edge) => {
            const a = positions[edge.source];
            const b = positions[edge.target];
            if (!a || !b) return null;
            const weight = edge.shared / maxShared;
            return (
              <line
                key={`${edge.source}|${edge.target}`}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke="currentColor"
                className="text-muted-foreground"
                strokeOpacity={0.15 + weight * 0.45}
                strokeWidth={1 + weight * 3}
              />
            );
          })}

          {nodes.map((node) => {
            const p = positions[node.id];
            if (!p) return null;
            const isCenter = node.id === center.id;
            const r = nodeRadius(node, isCenter);
            return (
              <g
                key={node.id}
                data-node-id={node.id}
                role="button"
                tabIndex={0}
                aria-label={t("app.graph.map.node", {
                  name: node.name,
                  type: node.type,
                  connections: isCenter ? related.length : 1,
                })}
                transform={`translate(${p.x} ${p.y})`}
                style={{ cursor: onSelect && !isCenter ? "pointer" : "default" }}
                onClick={() => !isCenter && onSelect?.(node.id)}
                onFocus={() => setFocused(node.id)}
                onBlur={() => setFocused((f) => (f === node.id ? null : f))}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    if (!isCenter) onSelect?.(node.id);
                  } else if (event.key === "ArrowRight" || event.key === "ArrowDown") {
                    event.preventDefault();
                    moveFocus(node.id, 1);
                  } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
                    event.preventDefault();
                    moveFocus(node.id, -1);
                  }
                }}
              >
                <circle
                  r={r}
                  fill={typeHex(node.type)}
                  fillOpacity={isCenter ? 1 : 0.85}
                  stroke="white"
                  strokeWidth={isCenter ? 2 : 1.5}
                  className={focused === node.id ? "outline outline-2 outline-offset-2 outline-primary" : ""}
                />
                <text y={r + 12} textAnchor="middle" fontSize={10} className="fill-foreground pointer-events-none">
                  {node.name.length > 14 ? `${node.name.slice(0, 13)}…` : node.name}
                </text>
              </g>
            );
          })}
        </g>
      </svg>

      <p className="text-[11px] text-muted-foreground">{t("app.graph.map.hint")}</p>
    </div>
  );
}
