import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

import training_collector
from training_collector import _is_clean, _insert, _stage_groups


# ── _is_clean ────────────────────────────────────────────────────────────────

def test_is_clean_accepts_clean_listing():
    assert _is_clean({"accident_count": 0, "title_status": "clean"}, "cargurus") is True


def test_is_clean_rejects_accidents():
    assert _is_clean({"accident_count": 1, "title_status": "clean"}, "cargurus") is False


def test_is_clean_rejects_bad_title():
    for status in ("salvage", "rebuilt", "flood", "lemon", "fleet"):
        assert _is_clean({"accident_count": 0, "title_status": status}, "cargurus") is False


def test_is_clean_requires_enrichment_for_required_sources():
    # accident_count is None (enrichment never ran) on cargurus -> reject
    assert _is_clean({"accident_count": None, "title_status": "clean"}, "cargurus") is False


def test_is_clean_dealer_source_does_not_require_enrichment():
    assert _is_clean({"accident_count": None, "title_status": "clean"}, "dealer") is True


def test_is_clean_missing_title_status_defaults_to_unknown_and_passes():
    assert _is_clean({"accident_count": 0}, "dealer") is True


# ── _stage_groups ─────────────────────────────────────────────────────────────

def test_stage_groups_groups_by_effective_min_year():
    # Both BMW X3 and Audi Q5 have a 2022 floor per cars.json, so a 2023 stage covers both
    vehicles = [("BMW", "X3"), ("Audi", "Q5")]
    groups = training_collector._stage_groups(vehicles, 2023)
    assert all(k >= 2023 for k in groups)
    all_models_in_groups = [m for models in groups.values() for m in models]
    assert set(all_models_in_groups) == set(vehicles)


def test_stage_groups_skips_models_above_stage_min_year_ceiling():
    # BMW X3 has max_year=2025 per cars.json; a stage starting at 2030 should skip it entirely
    vehicles = [("BMW", "X3")]
    groups = training_collector._stage_groups(vehicles, 2030)
    all_models_in_groups = [m for models in groups.values() for m in models]
    assert ("BMW", "X3") not in all_models_in_groups


def test_stage_groups_returns_sorted_dict():
    vehicles = [("BMW", "X3"), ("Audi", "Q5"), ("Volkswagen", "Tiguan")]
    groups = training_collector._stage_groups(vehicles, 2020)
    keys = list(groups.keys())
    assert keys == sorted(keys)


def test_stage_groups_empty_vehicle_list():
    assert training_collector._stage_groups([], 2020) == {}


# ── _insert ──────────────────────────────────────────────────────────────────

def _base_row(**overrides):
    row = {
        "make": "BMW", "model": "X3", "year": 2023, "mileage": 20000, "price": 40000,
        "trim": "xDrive30i", "source": "cargurus", "listing_url": "http://x/1",
        "condition": "used", "accident_count": 0, "title_status": "clean",
    }
    row.update(overrides)
    return row


def test_insert_accepts_valid_row():
    with patch.object(training_collector, "execute") as mock_execute:
        n = _insert([_base_row()], allowed_norm={"x3"})
    assert n == 1
    mock_execute.assert_called_once()


def test_insert_skips_row_missing_required_fields():
    with patch.object(training_collector, "execute") as mock_execute:
        n = _insert([_base_row(price=None)], allowed_norm={"x3"})
    assert n == 0
    mock_execute.assert_not_called()


def test_insert_skips_model_not_in_allowlist():
    with patch.object(training_collector, "execute") as mock_execute:
        n = _insert([_base_row()], allowed_norm={"q5"})
    assert n == 0
    mock_execute.assert_not_called()


def test_insert_enforces_min_year():
    with patch.object(training_collector, "execute") as mock_execute:
        n = _insert([_base_row(year=2020)], allowed_norm={"x3"}, min_year=2022)
    assert n == 0
    mock_execute.assert_not_called()


def test_insert_enforces_max_year():
    with patch.object(training_collector, "execute") as mock_execute:
        n = _insert([_base_row(year=2030)], allowed_norm={"x3"}, max_year=2025)
    assert n == 0
    mock_execute.assert_not_called()


def test_insert_enforces_price_floor():
    with patch.object(training_collector, "execute") as mock_execute:
        n = _insert([_base_row(price=15000)], allowed_norm={"x3"})
    assert n == 0
    mock_execute.assert_not_called()


def test_insert_enforces_mileage_floor():
    with patch.object(training_collector, "execute") as mock_execute:
        n = _insert([_base_row(mileage=500)], allowed_norm={"x3"})
    assert n == 0
    mock_execute.assert_not_called()


def test_insert_rejects_excessive_mileage_rate():
    # 2024 car (age ~2yrs) with 100k miles -> way over 14k/yr rental heuristic
    with patch.object(training_collector, "execute") as mock_execute:
        n = _insert([_base_row(year=2024, mileage=100000)], allowed_norm={"x3"})
    assert n == 0
    mock_execute.assert_not_called()


def test_insert_rejects_disallowed_trim():
    with patch.object(training_collector, "execute") as mock_execute:
        n = _insert([_base_row(trim="M40i")], allowed_norm={"x3"})  # M40i excluded for X3
    assert n == 0
    mock_execute.assert_not_called()


def test_insert_rejects_accident_or_bad_title():
    with patch.object(training_collector, "execute") as mock_execute:
        n = _insert([_base_row(accident_count=1)], allowed_norm={"x3"})
    assert n == 0
    mock_execute.assert_not_called()


def test_insert_continues_on_db_error():
    with patch.object(training_collector, "execute", side_effect=Exception("db error")):
        n = _insert([_base_row()], allowed_norm={"x3"})
    assert n == 0  # exception swallowed, not counted as inserted


def test_insert_processes_multiple_rows_independently():
    rows = [_base_row(listing_url="http://x/1"), _base_row(listing_url="http://x/2", price=5000)]
    with patch.object(training_collector, "execute") as mock_execute:
        n = _insert(rows, allowed_norm={"x3"})
    assert n == 1  # second row fails price floor
    mock_execute.assert_called_once()


def test_insert_xc60_recharge_uses_xc60_allowlist_entry():
    row = _base_row(model="XC60 Recharge", make="Volvo", trim="Plus", price=45000, mileage=15000)
    with patch.object(training_collector, "execute") as mock_execute:
        n = _insert([row], allowed_norm={"xc60"})
    assert n == 1
    mock_execute.assert_called_once()
