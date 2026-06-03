import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/db";
import type { Listing } from "@/lib/types";

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const status = searchParams.get("status") ?? "active";

  const statusFilter: Record<string, string[]> = {
    active: ["new", "active"],
    contacted: ["contacted"],
    dismissed: ["dismissed"],
    purchased: ["purchased"],
  };

  const statuses = statusFilter[status] ?? ["new", "active"];
  const placeholders = statuses.map((_, i) => `$${i + 1}`).join(", ");

  const rows = await query<Listing>(
    `SELECT l.*, ls.value_score, ls.market_median_price, ls.market_price_source, ls.shap_values
     FROM listings l
     LEFT JOIN listing_scores ls ON ls.listing_id = l.id
     WHERE l.status IN (${placeholders})
       AND (l.snoozed_until IS NULL OR l.snoozed_until < now())
     ORDER BY ls.value_score DESC NULLS LAST, l.first_seen_at DESC`,
    statuses
  );

  return NextResponse.json(rows);
}
