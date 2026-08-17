import { NextRequest, NextResponse } from "next/server";
import { query, queryOne } from "@/lib/db";
import type { Listing } from "@/lib/types";
import { spawn } from "child_process";
import fs from "fs";

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  const listing = await queryOne<Listing>(
    `SELECT l.*, ls.value_score, ls.fair_price, ls.kbb_price, ls.market_price_source, ls.shap_values
     FROM listings l
     LEFT JOIN listing_scores ls ON ls.listing_id = l.id
     WHERE l.id = $1`,
    [id]
  );
  if (!listing) return NextResponse.json({ error: "Not found" }, { status: 404 });

  return NextResponse.json(listing);
}

export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await req.json();
  const { status, snoozed_until, is_pinned, mileage, trim, year, price, drivetrain, user_notes } = body;

  const updates: string[] = [];
  const values: unknown[] = [];

  if (status) {
    updates.push(`status = $${values.length + 1}`);
    values.push(status);
  }
  if (snoozed_until !== undefined) {
    updates.push(`snoozed_until = $${values.length + 1}`);
    values.push(snoozed_until);
  }
  if (is_pinned !== undefined) {
    updates.push(`is_pinned = $${values.length + 1}`);
    values.push(is_pinned);
  }
  if (user_notes !== undefined) {
    updates.push(`user_notes = $${values.length + 1}`);
    values.push(user_notes);
  }
  // Editable listing fields — clear score after any change so it gets re-scored
  const scoreFields = ["mileage", "trim", "year", "price", "drivetrain"] as const;
  const editedScoreField = scoreFields.some(f => body[f] !== undefined);
  if (mileage    !== undefined) { updates.push(`mileage = $${values.length + 1}`);    values.push(mileage); }
  if (trim       !== undefined) { updates.push(`trim = $${values.length + 1}`);       values.push(trim); }
  if (year       !== undefined) { updates.push(`year = $${values.length + 1}`);       values.push(year); }
  if (price      !== undefined) { updates.push(`price = $${values.length + 1}`);      values.push(price); }
  if (drivetrain !== undefined) { updates.push(`drivetrain = $${values.length + 1}`); values.push(drivetrain); }

  if (updates.length === 0) return NextResponse.json({ error: "Nothing to update" }, { status: 400 });

  values.push(id);
  await query(`UPDATE listings SET ${updates.join(", ")} WHERE id = $${values.length}`, values);

  // Clear score and immediately re-run analysis so the new score is visible right away
  if (editedScoreField) {
    await query("DELETE FROM listing_scores WHERE listing_id = $1", [id]);
    await query("UPDATE listings SET status = 'new' WHERE id = $1 AND status = 'active'", [id]);
    const agentsDir = process.env.AGENTS_DIR ?? "agents";
    const venvPython = process.env.AGENTS_PYTHON ?? "python3";
    if (fs.existsSync(venvPython)) {
      const child = spawn(venvPython, ["-u", "analysis_agent.py"], {
        cwd: agentsDir,
        detached: true,
        stdio: "ignore",
        env: { ...process.env, PYTHONUNBUFFERED: "1" },
      });
      child.unref();
    }
  }

  return NextResponse.json({ ok: true });
}
