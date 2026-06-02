import { NextRequest, NextResponse } from "next/server";
import { spawn } from "child_process";
import { queryOne } from "@/lib/db";
import type { Listing } from "@/lib/types";

export async function POST(req: NextRequest) {
  const { listing_id } = await req.json();
  if (!listing_id) return NextResponse.json({ error: "listing_id required" }, { status: 400 });

  const listing = await queryOne<Listing>(
    `SELECT l.*, s.inquiry_template FROM listings l
     CROSS JOIN user_settings s
     WHERE l.id = $1 LIMIT 1`,
    [listing_id]
  );
  if (!listing) return NextResponse.json({ error: "Listing not found" }, { status: 404 });

  // Build inquiry text directly (no agent overhead for this simple operation)
  const template = (listing as any).inquiry_template as string;
  const priceFormatted = listing.price ? `$${listing.price.toLocaleString()}` : "listed price";
  const inquiryText = template
    .replace("{year}", String(listing.year))
    .replace("{make}", listing.make)
    .replace("{model}", listing.model)
    .replace("{price}", priceFormatted)
    .replace("{vin}", listing.vin ?? "N/A");

  // Log contact attempt via Python (fire and forget)
  const agentsDir = process.env.AGENTS_DIR ?? "/agents";
  const venvPython = process.env.AGENTS_PYTHON ?? "python3";
  const logScript = `
import sys, os
sys.path.insert(0, '${agentsDir}')
os.chdir('${agentsDir}')
from tools.db import execute
execute("INSERT INTO contact_log (listing_id, channel) VALUES (%s, 'contact_form')", ('${listing_id}',))
execute("UPDATE listings SET status = 'contacted' WHERE id = %s AND status NOT IN ('purchased','dismissed')", ('${listing_id}',))
`;
  const child = spawn(venvPython, ["-c", logScript], { detached: true, stdio: "ignore" });
  child.unref();

  return NextResponse.json({
    inquiry_text: inquiryText,
    listing_url: listing.listing_url,
    dealer_phone: listing.dealer_phone,
  });
}
