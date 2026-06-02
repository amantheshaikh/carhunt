import { NextRequest, NextResponse } from "next/server";
import { query, queryOne } from "@/lib/db";

export async function GET() {
  const row = await queryOne("SELECT * FROM user_settings LIMIT 1");
  return NextResponse.json(row ?? {});
}

export async function PATCH(req: NextRequest) {
  const body = await req.json();
  const allowed = ["zip_code", "radius_miles", "max_price", "max_mileage", "min_year", "max_year", "body_types", "inquiry_template"];
  const updates: string[] = [];
  const values: unknown[] = [];

  for (const [key, value] of Object.entries(body)) {
    if (allowed.includes(key)) {
      updates.push(`${key} = $${values.length + 1}`);
      values.push(value);
    }
  }

  if (updates.length === 0) return NextResponse.json({ error: "No valid fields" }, { status: 400 });

  await query(`UPDATE user_settings SET ${updates.join(", ")}`, values);
  return NextResponse.json({ ok: true });
}
