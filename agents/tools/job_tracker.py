"""Lightweight background job progress tracker — writes to background_jobs table."""
from tools.db import execute, execute_returning


def start_job(job_type: str, stage_total: int | None = None) -> int:
    row = execute_returning(
        "INSERT INTO background_jobs (job_type, stage_total, started_at) VALUES (%s, %s, now()) RETURNING id",
        (job_type, stage_total),
    )
    return row["id"]


def update_job(job_id: int, stage_current: int | None = None, stage_label: str | None = None, rows_added: int | None = None) -> None:
    execute(
        """UPDATE background_jobs SET
           stage_current = COALESCE(%s, stage_current),
           stage_label   = COALESCE(%s, stage_label),
           rows_added    = COALESCE(%s, rows_added)
           WHERE id = %s""",
        (stage_current, stage_label, rows_added, job_id),
    )


def complete_job(job_id: int, status: str = "complete") -> None:
    execute(
        "UPDATE background_jobs SET status = %s, completed_at = now() WHERE id = %s",
        (status, job_id),
    )
