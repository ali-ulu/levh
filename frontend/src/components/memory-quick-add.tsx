"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Loader2, Pin, Plus } from "lucide-react";

export function MemoryQuickAdd({ onAdded }: { onAdded?: () => void }) {
  const [content, setContent] = useState("");
  const [tags, setTags] = useState("");
  const [project, setProject] = useState("");
  const [importance, setImportance] = useState(0.5);
  const [pinned, setPinned] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    if (!content.trim() || saving) return;
    setSaving(true);
    setError("");
    try {
      await api.storeMemory({
        content: content.trim(),
        importance,
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
        project: project.trim() || undefined,
        pinned,
        source: "dashboard",
        memory_type: "episodic",
      });
      setContent("");
      setTags("");
      setPinned(false);
      onAdded?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to store memory");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Plus className="h-4 w-4" />
          Quick Add Memory
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Textarea
          placeholder="What should your AI remember? (a decision, a convention, a fact...)"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submit();
          }}
          rows={3}
        />
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Tags (comma-separated)</Label>
            <Input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="api, auth" />
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Project</Label>
            <Input
              value={project}
              onChange={(e) => setProject(e.target.value)}
              placeholder="my-repo"
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">
              Importance: {importance.toFixed(1)}
            </Label>
            <input
              type="range"
              min={0}
              max={1}
              step={0.1}
              value={importance}
              onChange={(e) => setImportance(parseFloat(e.target.value))}
              className="w-full accent-primary h-9"
            />
          </div>
        </div>
        <div className="flex items-center justify-between">
          <Button
            type="button"
            variant={pinned ? "default" : "outline"}
            size="sm"
            onClick={() => setPinned(!pinned)}
          >
            <Pin className="h-3.5 w-3.5 mr-1.5" />
            {pinned ? "Pinned — never decays" : "Pin memory"}
          </Button>
          <div className="flex items-center gap-3">
            {error && <span className="text-xs text-destructive">{error}</span>}
            <Button onClick={submit} disabled={!content.trim() || saving}>
              {saving && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Store memory
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
