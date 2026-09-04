import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

import tools.job_tracker as job_tracker


def test_start_job_inserts_and_returns_id():
    with patch.object(job_tracker, "execute_returning", return_value={"id": 7}) as mock_er:
        job_id = job_tracker.start_job("refresh", stage_total=4)
    assert job_id == 7
    sql, params = mock_er.call_args[0]
    assert "INSERT INTO background_jobs" in sql
    assert params == ("refresh", 4)


def test_start_job_default_stage_total_none():
    with patch.object(job_tracker, "execute_returning", return_value={"id": 1}) as mock_er:
        job_tracker.start_job("backfill")
    _, params = mock_er.call_args[0]
    assert params == ("backfill", None)


def test_update_job_passes_all_fields():
    with patch.object(job_tracker, "execute") as mock_execute:
        job_tracker.update_job(5, stage_current=2, stage_label="Scoring", rows_added=100)
    sql, params = mock_execute.call_args[0]
    assert "UPDATE background_jobs SET" in sql
    assert params == (2, "Scoring", 100, 5)


def test_update_job_partial_fields_default_to_none():
    with patch.object(job_tracker, "execute") as mock_execute:
        job_tracker.update_job(5, stage_current=3)
    _, params = mock_execute.call_args[0]
    assert params == (3, None, None, 5)


def test_complete_job_default_status():
    with patch.object(job_tracker, "execute") as mock_execute:
        job_tracker.complete_job(9)
    sql, params = mock_execute.call_args[0]
    assert "UPDATE background_jobs SET status" in sql
    assert params == ("complete", 9)


def test_complete_job_error_status():
    with patch.object(job_tracker, "execute") as mock_execute:
        job_tracker.complete_job(9, status="error")
    _, params = mock_execute.call_args[0]
    assert params == ("error", 9)
