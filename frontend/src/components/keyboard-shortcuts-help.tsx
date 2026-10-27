"use client";

// Keyboard shortcuts help (#568). The dashboard had a handful of shortcuts
// (⌘K, ⌘⇧A, Escape) and no way to discover them. This is the modal `?` opens:
// a categorised list, each row showing the Mac chord and its Windows/Linux
// equivalent, closing on Escape through the Dialog primitive.
//
// It is deliberately data-driven: a new shortcut is a row here, not new JSX.
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useT } from "@/lib/i18n";
import { Command, Keyboard } from "lucide-react";

type Shortcut = {
  /** Catalogue key for the action. */
  labelKey: string;
  /** Mac chord, e.g. ["⌘", "K"]. Each entry is a catalogue key, so the key
   * names ("Ctrl", "Enter") are translatable and the string ratchet stays at
   * zero for this file. */
  mac: string[];
  /** Windows/Linux chord, e.g. ["Ctrl", "K"]. */
  other: string[];
};

type Group = { titleKey: string; items: Shortcut[] };

// Chord label keys, kept in one place so a shortcut reads as data.
const K = {
  cmd: "shortcuts.key.cmd",
  shift: "shortcuts.key.shift",
  ctrl: "shortcuts.key.ctrl",
  enter: "shortcuts.key.enter",
  esc: "shortcuts.key.esc",
  tab: "shortcuts.key.tab",
  up: "shortcuts.key.up",
  down: "shortcuts.key.down",
  plus: "shortcuts.key.plus",
  minus: "shortcuts.key.minus",
  k: "shortcuts.key.k",
  a: "shortcuts.key.a",
  g: "shortcuts.key.g",
  o: "shortcuts.key.o",
  m: "shortcuts.key.m",
  s: "shortcuts.key.s",
  question: "shortcuts.key.question",
} as const;

const GROUPS: Group[] = [
  {
    titleKey: "shortcuts.group.global",
    items: [
      { labelKey: "shortcuts.action.openSearch", mac: [K.cmd, K.k], other: [K.ctrl, K.k] },
      { labelKey: "shortcuts.action.quickCapture", mac: [K.cmd, K.shift, K.a], other: [K.ctrl, K.shift, K.a] },
      { labelKey: "shortcuts.action.help", mac: [K.question], other: [K.question] },
      { labelKey: "shortcuts.action.close", mac: [K.esc], other: [K.esc] },
    ],
  },
  {
    titleKey: "shortcuts.group.navigation",
    items: [
      { labelKey: "shortcuts.action.overview", mac: [K.g, K.o], other: [K.g, K.o] },
      { labelKey: "shortcuts.action.memories", mac: [K.g, K.m], other: [K.g, K.m] },
      { labelKey: "shortcuts.action.graph", mac: [K.g, K.g], other: [K.g, K.g] },
      { labelKey: "shortcuts.action.settings", mac: [K.g, K.s], other: [K.g, K.s] },
    ],
  },
  {
    titleKey: "shortcuts.group.memory",
    items: [
      { labelKey: "shortcuts.action.moveDown", mac: [K.down], other: [K.down] },
      { labelKey: "shortcuts.action.moveUp", mac: [K.up], other: [K.up] },
      { labelKey: "shortcuts.action.open", mac: [K.enter], other: [K.enter] },
      { labelKey: "shortcuts.action.selectAll", mac: [K.cmd, K.a], other: [K.ctrl, K.a] },
    ],
  },
  {
    titleKey: "shortcuts.group.graph",
    items: [
      { labelKey: "shortcuts.action.zoomIn", mac: [K.plus], other: [K.plus] },
      { labelKey: "shortcuts.action.zoomOut", mac: [K.minus], other: [K.minus] },
      { labelKey: "shortcuts.action.resetView", mac: [K.esc], other: [K.esc] },
      { labelKey: "shortcuts.action.nextNode", mac: [K.tab], other: [K.tab] },
    ],
  },
];

function Keys({ keys, variant }: { keys: string[]; variant: "mac" | "other" }) {
  const t = useT();
  return (
    <span className="flex items-center gap-1">
      {keys.map((key) => (
        <kbd
          key={`${variant}-${key}`}
          className="rounded border bg-muted px-1.5 py-0.5 text-[11px] font-medium"
        >
          {t(key)}
        </kbd>
      ))}
    </span>
  );
}

export function KeyboardShortcutsHelp({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useT();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Keyboard className="h-5 w-5" /> {t("shortcuts.title")}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-5">
          {GROUPS.map((group) => (
            <div key={group.titleKey} className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-[0.13em] text-muted-foreground">
                {t(group.titleKey)}
              </p>
              <div className="space-y-1.5">
                {group.items.map((item) => (
                  <div
                    key={item.labelKey}
                    className="flex items-center justify-between gap-4 text-sm"
                  >
                    <span className="min-w-0 truncate">{t(item.labelKey)}</span>
                    <span className="flex shrink-0 items-center gap-4">
                      <Keys keys={item.mac} variant="mac" />
                      <Keys keys={item.other} variant="other" />
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Command className="h-3.5 w-3.5" aria-hidden="true" />
          {t("shortcuts.macHint")}
        </p>
      </DialogContent>
    </Dialog>
  );
}
