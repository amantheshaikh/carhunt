/** Shared drivetrain / powertrain tag detection — single source of truth for UI. */

export function getDrivetrain(trim: string | null, model: string | null): "AWD" | "FWD" | null {
  const t = `${trim ?? ""} ${model ?? ""}`.toLowerCase();
  if (t.includes("awd") || t.includes("4wd") || t.includes("4motion")) return "AWD";
  if (t.includes("fwd")) return "FWD";
  return null;
}

export function getPowertrain(trim: string | null, model: string | null): "PHEV" | "Hybrid" | null {
  const t = `${trim ?? ""} ${model ?? ""}`.toLowerCase();
  if (t.includes("plug-in") || t.includes("phev")) return "PHEV";
  if (t.includes("hybrid")) return "Hybrid";
  return null;
}

// ─── Deal categories ─────────────────────────────────────────────────────────

export type DealCategory = "Exceptional Deal" | "Great Deal" | "Good Deal" | "Fair Price" | "Poor Value" | null;

export function getDealCategory(score: number | null): DealCategory {
  if (score == null) return null;
  if (score >= 88) return "Exceptional Deal";
  if (score >= 72) return "Great Deal";
  if (score >= 58) return "Good Deal";
  if (score >= 45) return "Fair Price";
  return "Poor Value";
}

export function getDealCategoryStyle(score: number | null): string {
  if (score == null) return "bg-zinc-700 text-zinc-400";
  if (score >= 88) return "bg-emerald-900 text-emerald-300 border border-emerald-700";
  if (score >= 72) return "bg-green-900 text-green-300 border border-green-700";
  if (score >= 58) return "bg-blue-900 text-blue-300 border border-blue-700";
  if (score >= 45) return "bg-zinc-800 text-zinc-400 border border-zinc-700";
  return "bg-red-950 text-red-400 border border-red-900";
}

/** Lower-case tag for filter comparisons (matches Filters.drivetrain type). */
export function getDrivetrainTag(trim: string | null, model: string | null): "awd" | "fwd" | "" {
  const d = getDrivetrain(trim, model);
  return d ? (d.toLowerCase() as "awd" | "fwd") : "";
}

/** Lower-case tag for filter comparisons (matches Filters.powertrain type). */
export function getPowertrainTag(trim: string | null, model: string | null): "hybrid" | "gas" {
  const p = getPowertrain(trim, model);
  return p ? "hybrid" : "gas";
}
