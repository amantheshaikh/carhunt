import { NextResponse } from "next/server";
import { query } from "@/lib/db";

export interface JobStatus {
  id: number;
  job_type: string;
  status: string;
  stage_current: number | null;
  stage_total: number | null;
  stage_label: string | null;
  rows_added: number;
  started_at: string;
  completed_at: string | null;
}

export async function GET() {
  const rows = await query<JobStatus>(
    `SELECT id, job_type, status, stage_current, stage_total, stage_label,
            rows_added, started_at::text, completed_at::text
     FROM background_jobs
     ORDER BY started_at DESC
     LIMIT 1`
  );
  return NextResponse.json(rows[0] ?? null);
}
