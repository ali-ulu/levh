"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  ArrowRight,
  BookOpen,
  CheckCircle2,
  FileText,
  Lightbulb,
  Loader2,
  Pin,
  Plus,
  Sparkles,
  Zap,
} from "lucide-react";

const TEMPLATES = [
  {
    id: "decision",
    label: "Decision",
    icon: CheckCircle2,
    placeholder: "What did you decide and why?",
    example: "We chose SQLite over PostgreSQL because...",
    tags: ["decision", "architecture"],
  },
  {
    id: "convention",
    label: "Convention",
    icon: FileText,
    placeholder: "What's the rule or convention to follow?",
    example: "All API responses should use snake_case keys...",
    tags: ["convention", "standards"],
  },
  {
    id: "context",
    label: "Context",
    icon: BookOpen,
    placeholder: "What context should your AI remember?",
    example: "The auth module uses JWT tokens with 24h expiry...",
    tags: ["context"],
  },
  {
    id: "insight",
    label: "Insight",
    icon: Lightbulb,
    placeholder: "What did you learn or realize?",
    example: "The bug was caused by a race condition in...",
    tags: ["insight", "debugging"],
  },
];

export function MemoryQuickAdd({ onAdded }: { onAdded?: () => void }) {
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [project, setProject] = useState("");
  const [importance, setImportance] = useState(0.5);
  const [pinned, setPinned] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null);

  const selectTemplate = (templateId: string) => {
    const template = TEMPLATES.find((t) => t.id === templateId);
    if (template) {
      setSelectedTemplate(templateId);
      setTags(template.tags);
    }
  };

  const addTag = (tag: string) => {
    const trimmed = tag.trim().toLowerCase();
    if (trimmed && !tags.includes(trimmed)) {
      setTags((prev) => [...prev, trimmed]);
    }
  };

  const removeTag = (tag: string) => {
    setTags((prev) => prev.filter((t) => t !== tag));
  };

  const submit = async () => {
    if (!content.trim() || saving) return;
    setSaving(true);
    setError("");
    try {
      await api.storeMemory({
        content: content.trim(),
        importance,
        tags,
        project: project.trim() || undefined,
        pinned,
        source: "dashboard",
        memory_type: "episodic",
      });
      setContent("");
      setTags([]);
      setProject("");
      setPinned(false);
      setSelectedTemplate(null);
      setOpen(false);
      onAdded?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to store memory");
    } finally {
      setSaving(false);
    }
  };

  const reset = () => {
    setContent("");
    setTags([]);
    setProject("");
    setPinned(false);
    setSelectedTemplate(null);
    setError("");
  };

  const activeTemplate = TEMPLATES.find((t) => t.id === selectedTemplate);

  return (
    <>
      {/* Floating Action Button */}
      <button
        onClick={() => {
          reset();
          setOpen(true);
        }}
        className="fab-button"
        aria-label="Add new memory"
      >
        <Plus className="h-5 w-5" />
      </button>

      {/* Modal */}
      <Dialog
        open={open}
        onOpenChange={(v) => {
          setOpen(v);
          if (!v) reset();
        }}
      >
        <DialogContent className="max-w-lg sm:max-w-xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-lg">
              <Sparkles className="h-5 w-5 text-primary" />
              Add to your memory
            </DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            {/* Template picker */}
            {!selectedTemplate && (
              <div className="space-y-2">
                <Label className="text-xs text-muted-foreground">
                  What kind of memory?
                </Label>
                <div className="grid grid-cols-2 gap-2">
                  {TEMPLATES.map((template) => {
                    const Icon = template.icon;
                    return (
                      <button
                        key={template.id}
                        onClick={() => selectTemplate(template.id)}
                        className="template-card group"
                      >
                        <Icon className="h-4 w-4 text-muted-foreground group-hover:text-primary transition-colors" />
                        <div className="text-left">
                          <span className="text-sm font-medium">
                            {template.label}
                          </span>
                          <span className="block text-[11px] text-muted-foreground mt-0.5">
                            {template.placeholder}
                          </span>
                        </div>
                        <ArrowRight className="h-3.5 w-3.5 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Content input */}
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">
                {activeTemplate
                  ? activeTemplate.placeholder
                  : "What should your AI remember?"}
              </Label>
              <Textarea
                placeholder={
                  activeTemplate?.example ||
                  "A decision, convention, fact, or insight..."
                }
                value={content}
                onChange={(e) => setContent(e.target.value)}
                onKeyDown={(e) => {
                  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submit();
                }}
                rows={4}
                autoFocus
              />
            </div>

            {/* Tags */}
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">
                Tags{" "}
                <span className="text-muted-foreground/60">
                  (press Enter to add)
                </span>
              </Label>
              <div className="flex flex-wrap gap-1.5 mb-2">
                {tags.map((tag) => (
                  <span
                    key={tag}
                    className="tag-chip"
                    onClick={() => removeTag(tag)}
                  >
                    {tag}
                    <span className="ml-1 opacity-60">×</span>
                  </span>
                ))}
              </div>
              <Input
                placeholder="Add a tag..."
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addTag((e.target as HTMLInputElement).value);
                    (e.target as HTMLInputElement).value = "";
                  }
                }}
              />
            </div>

            {/* Project + Importance */}
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">
                  Project (optional)
                </Label>
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
                  className="w-full accent-primary h-8"
                />
              </div>
            </div>

            {/* Pin + Actions */}
            <div className="flex items-center justify-between pt-2 border-t">
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
                {error && (
                  <span className="text-xs text-destructive text-right leading-snug max-w-[200px]">
                    {error}
                  </span>
                )}
                {selectedTemplate && (
                  <Button variant="ghost" size="sm" onClick={reset}>
                    Back
                  </Button>
                )}
                <Button
                  onClick={submit}
                  disabled={!content.trim() || saving}
                  className="gap-2"
                >
                  {saving ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Zap className="h-4 w-4" />
                  )}
                  Store memory
                </Button>
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
