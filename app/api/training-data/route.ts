import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/db";

export interface TrainingRow {
  id: string;
  make: string;
  model: string;
  year: number;
  mileage: number | null;
  price: number;
  trim: string | null;
  powertrain: string | null;
  condition: string | null;
  source: string;
  collected_at: string;
  editable: boolean;  // true = price_training_data, false = listings
  listing_url: string | null;
}

export async function GET(req: NextRequest) {
  const model = req.nextUrl.searchParams.get("model");

  // Return distinct models list when no model param
  if (!model) {
    const rows = await query<{ model: string; n: number }>(
      `SELECT lower(model) as model, count(*)::int as n
       FROM price_training_data
       GROUP BY lower(model)
       ORDER BY lower(model)`
    );
    return NextResponse.json(rows);
  }

  const m = model.toLowerCase();

  // price_training_data rows (editable)
  const trainRows = await query<TrainingRow>(
    `SELECT id::text, make, model, year, mileage, price, trim, powertrain, condition, source, collected_at::text, listing_url
     FROM price_training_data
     WHERE lower(model) = $1
     ORDER BY year DESC, price`,
    [m]
  );
  const editable = trainRows.map((r) => ({ ...r, editable: true }));

  // listings rows (read-only reference — excluded models only use training data)
  const LISTINGS_EXCLUDED = new Set(["tucson hybrid"]);
  let listingRows: TrainingRow[] = [];
  if (!LISTINGS_EXCLUDED.has(m)) {
    const rows = await query<TrainingRow>(
      `SELECT id::text, make, model, year, mileage, price, trim, source, first_seen_at::text as collected_at, listing_url
       FROM listings
       WHERE lower(model) = $1
         AND condition = 'used'
         AND price BETWEEN 10000 AND 55000
         AND mileage <= 80000
       ORDER BY year DESC, price`,
      [m]
    );
    listingRows = rows.map((r) => ({ ...r, editable: false, source: "listing" }));
  }

  return NextResponse.json([...editable, ...listingRows]);
}

export async function DELETE(req: NextRequest) {
  const id = req.nextUrl.searchParams.get("id");
  if (!id) return NextResponse.json({ error: "id required" }, { status: 400 });

  await query(`DELETE FROM price_training_data WHERE id = $1`, [id]);
  return NextResponse.json({ ok: true });
}

export async function PATCH(req: NextRequest) {
  const id = req.nextUrl.searchParams.get("id");
  if (!id) return NextResponse.json({ error: "id required" }, { status: 400 });

  const body = await req.json();
  const allowed = ["price", "mileage", "trim", "year", "model", "powertrain"];
  const updates = Object.entries(body).filter(([k]) => allowed.includes(k));
  if (!updates.length) return NextResponse.json({ error: "no valid fields" }, { status: 400 });

  const setClauses = updates.map(([k], i) => `${k} = $${i + 1}`).join(", ");
  const values = [...updates.map(([, v]) => v), id];

  await query(
    `UPDATE price_training_data SET ${setClauses} WHERE id = $${values.length}`,
    values
  );
  return NextResponse.json({ ok: true });
}
