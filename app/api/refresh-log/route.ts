import { NextResponse } from "next/server";
import { query } from "@/lib/db";
import type { RefreshLog } from "@/lib/types";

export async function GET() {
  const rows = await query<RefreshLog>(
    "SELECT * FROM refresh_log ORDER BY started_at DESC LIMIT 10"
  );
  return NextResponse.json(rows);
}
