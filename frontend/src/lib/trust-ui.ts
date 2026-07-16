// Shared label → color mapping for trust/confidence UI (drawer badge, graph
// nodes, insights chart). Keeping this in one place means the same label
// always reads the same color everywhere in the dashboard.

const LABEL_CLASSES: Record<string, string> = {
  high: "border-emerald-500/60 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  medium_high: "border-green-500/60 bg-green-500/10 text-green-600 dark:text-green-400",
  medium: "border-amber-500/60 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  low: "border-orange-500/60 bg-orange-500/10 text-orange-600 dark:text-orange-400",
  very_low: "border-red-500/60 bg-red-500/10 text-red-600 dark:text-red-400",
};

const LABEL_HEX: Record<string, string> = {
  high: "#10b981", // emerald-500
  medium_high: "#22c55e", // green-500
  medium: "#f59e0b", // amber-500
  low: "#f97316", // orange-500
  very_low: "#ef4444", // red-500
};

/** Tailwind border/bg/text classes for a trust label pill/badge. */
export function trustLabelColor(label: string): string {
  return LABEL_CLASSES[label] ?? "border-muted-foreground/40 bg-muted text-muted-foreground";
}

/** Hex fill for contexts that need a raw color (inline SVG, recharts). */
export function trustLabelHex(label: string): string {
  return LABEL_HEX[label] ?? "#6b7280"; // gray-500 fallback
}

export const TRUST_LABEL_ORDER = ["high", "medium_high", "medium", "low", "very_low"] as const;

export function trustLabelText(label: string): string {
  return label.replace(/_/g, " ");
}
