/** Shared drivetrain / powertrain tag detection — single source of truth for UI. */

export function getDrivetrain(
  trim: string | null,
  model: string | null,
  dbDrivetrain?: string | null
): "AWD" | "FWD" | null {
  if (dbDrivetrain) {
    const d = dbDrivetrain.toLowerCase().trim();
    if (d === "awd" || d === "4wd") return "AWD";
    if (d === "fwd" || d === "rwd") return "FWD";
  }

  const t = ` ${trim ?? ""} ${model ?? ""} `.toLowerCase();
  if (
    t.includes("awd") ||
    t.includes("4wd") ||
    t.includes("4motion") ||
    t.includes("4x4") ||
    t.includes("all-wheel") ||
    t.includes("all wheel") ||
    t.includes("eawd") ||
    t.includes("xdrive") ||
    t.includes("m35i") ||
    t.includes("m40i") ||
    t.includes("quattro") ||
    t.includes("4matic") ||
    t.includes(" b6 ")
  ) {
    return "AWD";
  }
  if (
    t.includes("fwd") ||
    t.includes("front-wheel") ||
    t.includes("front wheel") ||
    t.includes("2wd") ||
    t.includes("rwd") ||
    t.includes("sdrive") ||
    t.includes(" b4 ")
  ) {
    return "FWD";
  }
  return null;
}

export function getPowertrain(trim: string | null, model: string | null): "PHEV" | "Hybrid" | "Electric" | null {
  const t = ` ${trim ?? ""} ${model ?? ""} `.toLowerCase();
  if (
    t.includes("plug-in") ||
    t.includes("phev") ||
    t.includes("plug in hybrid") ||
    t.includes("recharge") ||
    t.includes(" t8 ")
  ) {
    return "PHEV";
  }
  if (
    t.includes("hybrid") ||
    t.includes("350h") ||
    t.includes("400h") ||
    t.includes("500h")
  ) {
    return "Hybrid";
  }
  if (
    t.includes("electric") ||
    t.includes(" ev ") ||
    t.includes("bev") ||
    t.includes("ioniq")
  ) {
    return "Electric";
  }
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
export function getDrivetrainTag(
  trim: string | null,
  model: string | null,
  dbDrivetrain?: string | null
): "awd" | "fwd" | "" {
  const d = getDrivetrain(trim, model, dbDrivetrain);
  return d ? (d.toLowerCase() as "awd" | "fwd") : "";
}

/** Lower-case tag for filter comparisons (matches Filters.powertrain type). */
export function getPowertrainTag(trim: string | null, model: string | null): "hybrid" | "gas" {
  const p = getPowertrain(trim, model);
  return p === "PHEV" || p === "Hybrid" ? "hybrid" : "gas";
}

export const SIGMOID_COEFF = 11.0;

/** Calculate value score (0-100) from price and fair price using the pipeline's sigmoid function. */
export function getValueScore(price: number | null, fairPrice: number | null): number | null {
  if (!fairPrice || fairPrice <= 0 || !price) return null;
  const spread = (fairPrice - price) / fairPrice;
  return Math.round(100 / (1 + Math.exp(-spread * SIGMOID_COEFF)));
}

