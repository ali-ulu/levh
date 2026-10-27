"use client";

// A small, dependency-free toast system (#555). Every page caught its API
// errors with `console.error`, so a failed pin/delete/save was invisible to
// the person who triggered it. This is the surface that makes those failures
// visible without pulling in a toast library — the app already ships Radix
// primitives and this needs none of them.
//
// Rendering is deliberately simple: a portal to <body> so the sticky header's
// stacking context cannot clip it, a polite live region so a screen reader
// announces each message, and a module-level store so `toast.*` can be called
// from anywhere (including `lib/api.ts`, which has no React context).
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { CircleAlert, CheckCircle2, Info, Loader2, X } from "lucide-react";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export type ToastVariant = "success" | "error" | "info" | "loading";

export interface Toast {
  id: string;
  message: string;
  description?: string;
  variant: ToastVariant;
  duration: number;
}

export interface ToastOptions {
  description?: string;
  duration?: number;
}

interface ToastEntry {
  id: string;
  message: string;
  description?: string;
  variant: ToastVariant;
  duration: number;
}

// The store lives outside React so `toast.error()` works from a plain module.
// `getSnapshot` returns a frozen array that only changes identity when the
// list changes, which is what `useSyncExternalStore` needs to avoid an
// infinite render loop.
let toasts: ToastEntry[] = [];
const listeners = new Set<() => void>();
let counter = 0;

function emit() {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): ToastEntry[] {
  return toasts;
}

function nextId(): string {
  counter += 1;
  return `toast-${counter}`;
}

function addToast(variant: ToastVariant, message: string, options: ToastOptions = {}): string {
  const id = nextId();
  const duration = options.duration ?? (variant === "loading" ? 0 : 4000);
  toasts = [...toasts, { id, message, description: options.description, variant, duration }];
  emit();
  return id;
}

export function dismissToast(id: string) {
  toasts = toasts.filter((entry) => entry.id !== id);
  emit();
}

// A loading toast is meant to be replaced by its outcome: `const id =
// toast.loading("Saving…"); …; toast.success("Saved", { id })`.
export function updateToast(id: string, variant: ToastVariant, message: string, options: ToastOptions = {}) {
  const duration = options.duration ?? (variant === "loading" ? 0 : 4000);
  toasts = toasts.map((entry) =>
    entry.id === id
      ? { ...entry, variant, message, description: options.description, duration }
      : entry
  );
  emit();
}

export const toast = {
  success: (message: string, options?: ToastOptions) => addToast("success", message, options),
  error: (message: string, options?: ToastOptions) => addToast("error", message, options),
  info: (message: string, options?: ToastOptions) => addToast("info", message, options),
  loading: (message: string, options?: ToastOptions) => addToast("loading", message, options),
  dismiss: dismissToast,
};

const ICONS = {
  success: CheckCircle2,
  error: CircleAlert,
  info: Info,
  loading: Loader2,
} as const;

function ToastItem({ entry }: { entry: ToastEntry }) {
  const t = useT();
  const Icon = ICONS[entry.variant];
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (entry.duration <= 0) return;
    timerRef.current = setTimeout(() => dismissToast(entry.id), entry.duration);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [entry.id, entry.duration]);

  return (
    <div
      role="status"
      className={cn(
        "toast-item pointer-events-auto flex w-full items-start gap-3 rounded-xl border bg-background/95 p-3.5 shadow-lg backdrop-blur",
        `toast-${entry.variant}`
      )}
    >
      <Icon
        className={cn(
          "mt-0.5 h-4 w-4 shrink-0",
          entry.variant === "loading" && "animate-spin",
          entry.variant === "success" && "text-emerald-500",
          entry.variant === "error" && "text-destructive",
          entry.variant === "info" && "text-primary"
        )}
        aria-hidden="true"
      />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium break-words">{entry.message}</p>
        {entry.description && (
          <p className="mt-0.5 text-xs text-muted-foreground break-words">{entry.description}</p>
        )}
      </div>
      <button
        type="button"
        onClick={() => dismissToast(entry.id)}
        aria-label={t("ui.toast.dismiss")}
        className="rounded-md p-0.5 text-muted-foreground opacity-70 transition-opacity hover:opacity-100"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

export function Toaster() {
  const entries = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  // Portalling needs a DOM target; the server render (static export) has none,
  // so the first paint is empty and the client takes over on mount.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const content = useMemo(
    () => (
      <div
        className="pointer-events-none fixed inset-x-0 bottom-0 z-[100] flex flex-col items-center gap-2 p-4 sm:inset-x-auto sm:right-0 sm:top-0 sm:items-end sm:bottom-auto"
        aria-live="polite"
        aria-atomic="false"
      >
        {entries.map((entry) => (
          <ToastItem key={entry.id} entry={entry} />
        ))}
      </div>
    ),
    [entries]
  );

  if (!mounted || typeof document === "undefined") return null;
  return createPortal(content, document.body);
}

/** Test seam: clear the store between renders. */
export function __resetToasts() {
  toasts = [];
  emit();
}
