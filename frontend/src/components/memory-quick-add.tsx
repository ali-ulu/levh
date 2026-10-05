"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
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
    labelKey: "memoryQuickAdd.template.decision.label",
    icon: CheckCircle2,
    placeholderKey: "memoryQuickAdd.template.decision.placeholder",
    exampleKey: "memoryQuickAdd.template.decision.example",
    tags: ["decision", "architecture"],
  },
  {
    id: "convention",
    labelKey: "memoryQuickAdd.template.convention.label",
    icon: FileText,
    placeholderKey: "memoryQuickAdd.template.convention.placeholder",
    exampleKey: "memoryQuickAdd.template.convention.example",
    tags: ["convention", "standards"],
  },
  {
    id: "context",
    labelKey: "memoryQuickAdd.template.context.label",
    icon: BookOpen,
    placeholderKey: "memoryQuickAdd.template.context.placeholder",
    exampleKey: "memoryQuickAdd.template.context.example",
    tags: ["context"],
  },
  {
    id: "insight",
    labelKey: "memoryQuickAdd.template.insight.label",
    icon: Lightbulb,
    placeholderKey: "memoryQuickAdd.template.insight.placeholder",
    exampleKey: "memoryQuickAdd.template.insight.example",
    tags: ["insight", "debugging"],
  },
];

export function MemoryQuickAdd({ onAdded }: { onAdded?: () => void }) {
  const t = useT();
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
      setError(e instanceof Error ? e.message : t("memoryQuickAdd.error.storeFailed"));
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

  // Listen for hash #quick-capture to auto-open
  useEffect(() => {
    const onHash = () => {
      if (window.location.hash === "#quick-capture") {
        reset();
        setOpen(true);
      }
    };
    onHash();
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <div id="quick-capture">
      {/* Floating Action Button */}
      <button
        onClick={() => {
          reset();
          setOpen(true);
        }}
        className="fab-button"
        aria-label={t("memoryQuickAdd.addNewMemory")}
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
              {t("memoryQuickAdd.title")}
            </DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            {/* Template picker */}
            {!selectedTemplate && (
              <div className="space-y-2">
                <Label className="text-xs text-muted-foreground">
                  {t("memoryQuickAdd.kindPrompt")}
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
                            {t(template.labelKey)}
                          </span>
                          <span className="block text-[11px] text-muted-foreground mt-0.5">
                            {t(template.placeholderKey)}
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
                  ? t(activeTemplate.placeholderKey)
                  : t("memoryQuickAdd.contentPrompt")}
              </Label>
              <Textarea
                placeholder={
                  activeTemplate
                    ? t(activeTemplate.exampleKey)
                    : t("memoryQuickAdd.contentPlaceholder")
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
                {t("memoryQuickAdd.tags")}{" "}
                <span className="text-muted-foreground/60">
                  {t("memoryQuickAdd.tagsHint")}
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
                placeholder={t("memoryQuickAdd.addTagPlaceholder")}
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
                  {t("memoryQuickAdd.projectOptional")}
                </Label>
                <Input
                  value={project}
                  onChange={(e) => setProject(e.target.value)}
                  placeholder={t("memoryQuickAdd.projectPlaceholder")}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">
                  {t("memoryQuickAdd.importance", { value: importance.toFixed(1) })}
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
                {pinned ? t("memoryQuickAdd.pinned") : t("memoryQuickAdd.pin")}
              </Button>

              <div className="flex items-center gap-3">
                {error && (
                  <span className="text-xs text-destructive text-right leading-snug max-w-[200px]">
                    {error}
                  </span>
                )}
                {selectedTemplate && (
                  <Button variant="ghost" size="sm" onClick={reset}>
                    {t("memoryQuickAdd.back")}
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
                  {t("memoryQuickAdd.store")}
                </Button>
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
