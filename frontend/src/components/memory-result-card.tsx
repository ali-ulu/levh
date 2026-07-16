"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Memory } from "@/types";
import { Eye, FolderGit2, Pin, Sparkles } from "lucide-react";

function scoreColor(score?: number | null) {
  if (score === undefined || score === null) return "";
  if (score <= 0.35) return "border-green-500/60 text-green-600 dark:text-green-400";
  if (score <= 0.6) return "border-yellow-500/60 text-yellow-600 dark:text-yellow-400";
  return "border-red-500/60 text-red-600 dark:text-red-400";
}

export function MemoryResultCard({
  memory,
  score,
  onView,
}: {
  memory: Memory;
  score?: number;
  onView?: () => void;
}) {
  return (
    <div className="border rounded-lg p-3 bg-card hover:bg-accent/40 transition-colors">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm leading-relaxed line-clamp-3 flex-1">{memory.content}</p>
        {onView && (
          <Button variant="ghost" size="icon" className="shrink-0 h-7 w-7" onClick={onView} aria-label="View details">
            <Eye className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1.5 mt-2">
        {score !== undefined && (
          <Badge variant="outline" className={`text-[11px] font-mono ${scoreColor(score)}`}>
            H {score.toFixed(3)}
          </Badge>
        )}
        {memory.pinned && (
          <Badge variant="secondary" className="text-[11px]">
            <Pin className="h-2.5 w-2.5 mr-1" />
            pinned
          </Badge>
        )}
        {memory.metadata?.demo === true && (
          <Badge variant="outline" className="text-[11px] border-primary/40 text-primary">
            <Sparkles className="h-2.5 w-2.5 mr-1" />
            Demo data
          </Badge>
        )}
        <Badge variant={memory.memory_type === "episodic" ? "default" : "secondary"} className="text-[11px]">
          {memory.memory_type === "episodic" ? "episodic" : "short-term"}
        </Badge>
        {memory.project && (
          <Badge variant="outline" className="text-[11px]">
            <FolderGit2 className="h-2.5 w-2.5 mr-1" />
            {memory.project}
          </Badge>
        )}
        {memory.tags.slice(0, 4).map((t) => (
          <Badge key={t} variant="outline" className="text-[11px]">
            {t}
          </Badge>
        ))}
      </div>
    </div>
  );
}
