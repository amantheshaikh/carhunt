import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

import pytest

import backfill_training


# ── _resolve_model ───────────────────────────────────────────────────────────

def test_resolve_model_case_insensitive_match():
    make, model = backfill_training._resolve_model("x3")
    assert make == "BMW"
    assert model == "X3"


def test_resolve_model_matches_with_spaces_and_case():
    make, model = backfill_training._resolve_model("tucson hybrid")
    assert make == "Hyundai"
    assert model == "Tucson Hybrid"


def test_resolve_model_unknown_exits():
    with pytest.raises(SystemExit):
        backfill_training._resolve_model("Not A Real Model")


# ── _print_stats ─────────────────────────────────────────────────────────────

def test_print_stats_sums_counts():
    rows = [
        {"drivetrain": "awd", "powertrain": "gas", "n": 5},
        {"drivetrain": "fwd", "powertrain": "gas", "n": 3},
    ]
    with patch.object(backfill_training, "fetch_all", return_value=rows) as mock_fetch_all:
        total = backfill_training._print_stats("X3")
    assert total == 8
    sql, params = mock_fetch_all.call_args[0]
    assert "year = %s" not in sql  # no target_year -> no year clause
    assert params == ("x3",)


def test_print_stats_with_target_year_adds_clause():
    with patch.object(backfill_training, "fetch_all", return_value=[]) as mock_fetch_all:
        total = backfill_training._print_stats("X3", target_year=2023)
    assert total == 0
    sql, params = mock_fetch_all.call_args[0]
    assert "year = %s" in sql
    assert params == ("x3", 2023)


# ── rederive ─────────────────────────────────────────────────────────────────

def _training_row(**overrides):
    row = {
        "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "make": "BMW", "model": "X3", "year": 2023, "trim": "30i xDrive",
        "drivetrain": "unknown", "powertrain": "gas", "condition": "used", "cpo": False,
        "listing_url": "http://x/1", "listing_cpo": None, "listing_raw": None,
    }
    row.update(overrides)
    return row


def test_rederive_updates_changed_rows():
    rows = [_training_row()]
    with patch.object(backfill_training, "fetch_all", return_value=rows), \
         patch.object(backfill_training, "execute_values") as mock_ev:
        backfill_training.rederive("BMW", "X3", dry_run=False, job_id=None)
    mock_ev.assert_called_once()
    updates = mock_ev.call_args[0][1]
    assert len(updates) == 1
    new_dt, new_pt, new_trim, new_cpo, row_id = updates[0]
    assert new_dt == "awd"  # "30i xDrive" -> xDrive30i -> awd keyword detected
    assert new_trim == "xDrive30i"


def test_rederive_dry_run_does_not_call_execute_values():
    rows = [_training_row()]
    with patch.object(backfill_training, "fetch_all", return_value=rows), \
         patch.object(backfill_training, "execute_values") as mock_ev:
        backfill_training.rederive("BMW", "X3", dry_run=True, job_id=None)
    mock_ev.assert_not_called()


def test_rederive_no_changes_needed_skips_update():
    # Row already has correct derived values -> no update needed
    row = _training_row(trim="xDrive30i", drivetrain="awd", powertrain="gas", cpo=False)
    with patch.object(backfill_training, "fetch_all", return_value=[row]), \
         patch.object(backfill_training, "execute_values") as mock_ev:
        backfill_training.rederive("BMW", "X3", dry_run=False, job_id=None)
    mock_ev.assert_not_called()


def test_rederive_never_downgrades_specific_drivetrain_to_unknown():
    # Trim text alone can't determine drivetrain ("Premium" has no dt keyword) ->
    # should keep the existing stored value instead of overwriting with "unknown"
    row = _training_row(trim="Premium", drivetrain="awd", powertrain="gas")
    with patch.object(backfill_training, "fetch_all", return_value=[row]), \
         patch.object(backfill_training, "execute_values") as mock_ev:
        backfill_training.rederive("BMW", "X3", dry_run=False, job_id=None)
    # trim changes from "Premium" to canonical form only if base_trims maps it; drivetrain must stay "awd"
    if mock_ev.called:
        new_dt = mock_ev.call_args[0][1][0][0]
        assert new_dt == "awd"


def test_rederive_updates_job_progress_when_job_id_given():
    with patch.object(backfill_training, "fetch_all", return_value=[]), \
         patch.object(backfill_training, "update_job") as mock_update_job:
        backfill_training.rederive("BMW", "X3", dry_run=False, job_id=42)
    mock_update_job.assert_called_once()
    assert mock_update_job.call_args[0][0] == 42


def test_rederive_target_year_filters_query():
    with patch.object(backfill_training, "fetch_all", return_value=[]) as mock_fetch_all:
        backfill_training.rederive("BMW", "X3", dry_run=False, job_id=None, target_year=2024)
    sql, params = mock_fetch_all.call_args[0]
    assert "year = %s" in sql
    assert params == ("x3", 2024)


# ── retrain_one ──────────────────────────────────────────────────────────────

def test_retrain_one_skips_unknown_model():
    with patch.object(backfill_training, "load_training_data_for") as mock_load:
        backfill_training.retrain_one("not-a-real-model-xyz", job_id=None)
    mock_load.assert_not_called()


def test_retrain_one_trains_and_refreshes_medians():
    with patch.object(backfill_training, "_ALL_MODELS", {"x3"}), \
         patch.object(backfill_training, "load_training_data_for", return_value=([[1, 2]], [30000])) as mock_load, \
         patch.object(backfill_training, "_fit_stacked") as mock_fit, \
         patch.object(backfill_training, "refresh_self_computed_medians", return_value=5) as mock_refresh:
        backfill_training.retrain_one("X3", job_id=None)
    mock_load.assert_called_once_with("x3")
    mock_fit.assert_called_once()
    mock_refresh.assert_called_once()


def test_retrain_one_updates_job_progress():
    with patch.object(backfill_training, "_ALL_MODELS", {"x3"}), \
         patch.object(backfill_training, "load_training_data_for", return_value=([], [])), \
         patch.object(backfill_training, "_fit_stacked"), \
         patch.object(backfill_training, "refresh_self_computed_medians", return_value=0), \
         patch.object(backfill_training, "update_job") as mock_update_job:
        backfill_training.retrain_one("X3", job_id=7, stage_offset=2)
    stage_currents = [call.kwargs.get("stage_current") for call in mock_update_job.call_args_list]
    assert stage_currents == [3, 4]  # stage_offset + 1, stage_offset + 2
