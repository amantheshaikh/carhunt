import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import sourcing_agent


def setup_function(_fn):
    sourcing_agent._refresh_log_cache = None
    sourcing_agent._FORCE_MODE = False


# ── _should_skip_source ──────────────────────────────────────────────────────

def test_should_skip_source_force_mode_bypasses_everything():
    sourcing_agent._FORCE_MODE = True
    with patch.object(sourcing_agent, "fetch_one") as mock_fetch_one:
        skip, reason = sourcing_agent._should_skip_source("cargurus")
    assert skip is False
    assert reason == "force mode"
    mock_fetch_one.assert_not_called()


def test_should_skip_source_no_previous_run():
    with patch.object(sourcing_agent, "fetch_one", return_value=None):
        skip, reason = sourcing_agent._should_skip_source("cargurus")
    assert skip is False
    assert reason == "no previous run"


def test_should_skip_source_last_run_too_old():
    old_time = datetime.now(timezone.utc) - timedelta(hours=10)
    with patch.object(sourcing_agent, "fetch_one", return_value={
        "completed_at": old_time, "source_results": {"cargurus": 50}, "source_errors": {},
    }):
        skip, reason = sourcing_agent._should_skip_source("cargurus")
    assert skip is False
    assert "ago" in reason


def test_should_skip_source_had_error_last_run():
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    with patch.object(sourcing_agent, "fetch_one", side_effect=[
        {"completed_at": recent, "source_results": {"cargurus": 50}, "source_errors": {"cargurus": "timeout"}},
    ]):
        skip, reason = sourcing_agent._should_skip_source("cargurus")
    assert skip is False
    assert "error" in reason


def test_should_skip_source_recent_with_active_listings():
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    with patch.object(sourcing_agent, "fetch_one", side_effect=[
        {"completed_at": recent, "source_results": {"cargurus": 50}, "source_errors": {}},
        {"n": 5},
    ]):
        skip, reason = sourcing_agent._should_skip_source("cargurus")
    assert skip is True
    assert "active listings" in reason


def test_should_skip_source_recent_but_no_active_listings():
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    with patch.object(sourcing_agent, "fetch_one", side_effect=[
        {"completed_at": recent, "source_results": {"cargurus": 50}, "source_errors": {}},
        {"n": 0},
    ]):
        skip, reason = sourcing_agent._should_skip_source("cargurus")
    assert skip is False
    assert reason == "no active listings from this source"


def test_should_skip_source_caches_refresh_log_across_calls():
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    with patch.object(sourcing_agent, "fetch_one", side_effect=[
        {"completed_at": recent, "source_results": {}, "source_errors": {}},
        {"n": 0},
        {"n": 0},
    ]) as mock_fetch_one:
        sourcing_agent._should_skip_source("cargurus")
        sourcing_agent._should_skip_source("carscom")
    # Only 3 calls total: 1 for the shared refresh_log row + 1 active-count check per source
    assert mock_fetch_one.call_count == 3


def test_should_skip_source_naive_completed_at_treated_as_utc():
    naive_recent = datetime.now() - timedelta(hours=1)  # no tzinfo
    with patch.object(sourcing_agent, "fetch_one", side_effect=[
        {"completed_at": naive_recent, "source_results": {}, "source_errors": {}},
        {"n": 0},
    ]):
        skip, reason = sourcing_agent._should_skip_source("cargurus")
    assert skip is False  # should not raise on tz-naive datetime


# ── _get_known_ids / _get_known_urls_and_vins / _get_known_fingerprints ──────

def test_get_known_ids_builds_price_map():
    rows = [{"external_id": "a1", "price": 30000}, {"external_id": "a2", "price": 25000}]
    with patch.object(sourcing_agent, "fetch_all", return_value=rows):
        result = sourcing_agent._get_known_ids("cargurus")
    assert result == {"a1": 30000, "a2": 25000}


def test_get_known_urls_and_vins_filters_none():
    rows = [
        {"listing_url": "http://a", "vin": "VIN1"},
        {"listing_url": None, "vin": "VIN2"},
        {"listing_url": "http://b", "vin": None},
    ]
    with patch.object(sourcing_agent, "fetch_all", return_value=rows):
        urls, vins = sourcing_agent._get_known_urls_and_vins()
    assert urls == {"http://a", "http://b"}
    assert vins == {"VIN1", "VIN2"}


def test_get_known_fingerprints_builds_both_sets():
    rows = [
        {"make": "BMW", "model": "X3", "year": 2023, "mileage": 20000, "price": 40000, "dealer_name": "Jim Ellis"},
        {"make": "Audi", "model": "Q5", "year": 2024, "mileage": None, "price": 38000, "dealer_name": "Nalley"},
    ]
    with patch.object(sourcing_agent, "fetch_all", return_value=rows):
        price_fps, dealer_fps = sourcing_agent._get_known_fingerprints("dealer")
    assert (2023, 20000, 40000) in price_fps
    assert ("Jim Ellis", "BMW", "X3", 2023, 20000) in dealer_fps
    # Second row has mileage=None so it's excluded from dealer_fps but not price_fps
    assert len(dealer_fps) == 1


# ── _enrich_trim_from_vin ────────────────────────────────────────────────────

def test_enrich_trim_from_vin_skips_when_no_vin():
    listing = {"make": "BMW", "model": "X3", "trim": None}
    with patch.object(sourcing_agent, "decode_vin") as mock_decode:
        sourcing_agent._enrich_trim_from_vin(listing)
    mock_decode.assert_not_called()
    assert listing["trim"] is None


def test_enrich_trim_from_vin_skips_when_trim_already_valid():
    listing = {"make": "BMW", "model": "X3", "trim": "xDrive30i", "vin": "5UX43DP07R9T66468"}
    with patch.object(sourcing_agent, "decode_vin") as mock_decode:
        sourcing_agent._enrich_trim_from_vin(listing)
    mock_decode.assert_not_called()


def test_enrich_trim_from_vin_resolves_valid_trim():
    # Starting trim is invalid (M40i excluded for X3) so the early-return guard doesn't fire
    listing = {"make": "BMW", "model": "X3", "trim": "M40i", "vin": "5UX43DP07R9T66468"}
    with patch.object(sourcing_agent, "decode_vin", return_value={"trim": "xDrive30i"}), \
         patch.object(sourcing_agent, "canonical_trim", return_value="xDrive30i"):
        sourcing_agent._enrich_trim_from_vin(listing)
    assert listing["trim"] == "xDrive30i"


def test_enrich_trim_from_vin_discards_invalid_nhtsa_trim():
    listing = {"make": "BMW", "model": "X3", "trim": "M40i", "vin": "5UX43DP07R9T66468"}
    with patch.object(sourcing_agent, "decode_vin", return_value={"trim": "M35i"}), \
         patch.object(sourcing_agent, "canonical_trim", return_value="M35i"):
        sourcing_agent._enrich_trim_from_vin(listing)
    assert listing["trim"] == "M40i"  # M35i also not in allowlist -> original trim untouched


def test_enrich_trim_from_vin_no_nhtsa_trim_leaves_untouched():
    listing = {"make": "BMW", "model": "X3", "trim": "M40i", "vin": "5UX43DP07R9T66468"}
    with patch.object(sourcing_agent, "decode_vin", return_value={}):
        sourcing_agent._enrich_trim_from_vin(listing)
    assert listing["trim"] == "M40i"


def test_enrich_trim_from_vin_missing_trim_is_treated_as_already_valid():
    # is_trim_allowed(make, model, None) is True by design ("can't filter what we don't know"),
    # so a completely missing trim short-circuits before any NHTSA lookup.
    listing = {"make": "BMW", "model": "X3", "trim": None, "vin": "5UX43DP07R9T66468"}
    with patch.object(sourcing_agent, "decode_vin") as mock_decode:
        sourcing_agent._enrich_trim_from_vin(listing)
    mock_decode.assert_not_called()
    assert listing["trim"] is None


def test_enrich_trim_from_vin_skips_malformed_vin():
    listing = {"make": "BMW", "model": "X3", "trim": None, "vin": "TOOSHORT"}
    with patch.object(sourcing_agent, "decode_vin") as mock_decode:
        sourcing_agent._enrich_trim_from_vin(listing)
    mock_decode.assert_not_called()


# ── _save_new ────────────────────────────────────────────────────────────────

def test_save_new_inserts_listing():
    listing = {
        "source": "cargurus", "external_id": "123", "vin": "5UX43DP07R9T66468",
        "make": "BMW", "model": "X3", "trim": "xDrive30i", "year": 2023,
        "mileage": 20000, "price": 40000, "listing_url": "http://x",
        "title_status": "clean", "accident_count": 0,
    }
    with patch.object(sourcing_agent, "execute") as mock_execute:
        sourcing_agent._save_new(listing)
    mock_execute.assert_called_once()
    sql = mock_execute.call_args[0][0]
    assert "INSERT INTO listings" in sql


def test_save_new_swallows_unique_violation():
    from psycopg2.errors import UniqueViolation
    listing = {
        "source": "cargurus", "external_id": "123", "vin": None,
        "make": "BMW", "model": "X3", "trim": "xDrive30i", "year": 2023,
        "mileage": 20000, "price": 40000, "listing_url": "http://x",
        "title_status": "clean", "accident_count": 0,
    }
    with patch.object(sourcing_agent, "execute", side_effect=UniqueViolation("dup")):
        sourcing_agent._save_new(listing)  # should not raise
