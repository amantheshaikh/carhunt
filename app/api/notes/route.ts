import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/db";

export async function POST(req: NextRequest) {
  const { listing_id, note } = await req.json();
  if (!listing_id || !note) return NextResponse.json({ error: "listing_id and note required" }, { status: 400 });

  await query(
    "INSERT INTO contact_log (listing_id, channel, note) VALUES ($1, 'other', $2)",
    [listing_id, note]
  );

  return NextResponse.json({ ok: true });
}
