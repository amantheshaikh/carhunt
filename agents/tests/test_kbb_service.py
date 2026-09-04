import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
from unittest.mock import patch

import tools.kbb_service as kbb_service
from tools.kbb_service import predict_kbb_fair_price, update_kbb_market_cache, _get_base_msrp

_CURRENT_YEAR = datetime.now().year


def test_predict_kbb_missing_required_fields_returns_none():
    assert predict_kbb_fair_price({"make": "BMW", "model": "X3"}) is None  # no year
    assert predict_kbb_fair_price({"make": "BMW", "year": 2023}) is None   # no model
    assert predict_kbb_fair_price({"model": "X3", "year": 2023}) is None  # no make


def test_predict_kbb_returns_int_within_sanity_bounds():
    listing = {"make": "BMW", "model": "X3", "year": 2023, "mileage": 20000, "trim": "xDrive30i"}
    price = predict_kbb_fair_price(listing)
    assert isinstance(price, int)
    assert 10000 <= price <= 75000


def test_predict_kbb_newer_car_worth_more_than_older_same_config():
    base = {"make": "BMW", "model": "X3", "mileage": 20000, "trim": "xDrive30i"}
    newer = predict_kbb_fair_price({**base, "year": _CURRENT_YEAR - 1})
    older = predict_kbb_fair_price({**base, "year": _CURRENT_YEAR - 4})
    assert newer > older


def test_predict_kbb_lower_mileage_worth_more():
    base = {"make": "BMW", "model": "X3", "year": 2023, "trim": "xDrive30i"}
    low_miles = predict_kbb_fair_price({**base, "mileage": 5000})
    high_miles = predict_kbb_fair_price({**base, "mileage": 50000})
    assert low_miles > high_miles


def test_predict_kbb_awd_worth_more_than_fwd_same_trim_text():
    # Use explicit drivetrain field to isolate the AWD adjustment
    base = {"make": "BMW", "model": "X3", "year": 2023, "mileage": 20000, "trim": "sDrive30i"}
    awd = predict_kbb_fair_price({**base, "drivetrain": "awd"})
    fwd = predict_kbb_fair_price({**base, "drivetrain": "fwd"})
    assert awd > fwd


def test_predict_kbb_hybrid_and_phev_worth_more_than_gas():
    base = {"make": "Kia", "model": "Sportage Hybrid", "year": 2024, "mileage": 20000}
    gas = predict_kbb_fair_price({**base, "powertrain": "gas"})
    hybrid = predict_kbb_fair_price({**base, "powertrain": "hybrid"})
    phev = predict_kbb_fair_price({**base, "powertrain": "phev"})
    assert hybrid > gas
    assert phev > hybrid


def test_predict_kbb_cpo_adds_premium():
    base = {"make": "Audi", "model": "Q5", "year": 2023, "mileage": 20000, "trim": "Premium Plus"}
    non_cpo = predict_kbb_fair_price({**base, "cpo": False})
    cpo = predict_kbb_fair_price({**base, "cpo": True})
    assert cpo > non_cpo


def test_predict_kbb_cpo_from_insights_dict():
    base = {"make": "Audi", "model": "Q5", "year": 2023, "mileage": 20000, "trim": "Premium Plus"}
    via_insights = predict_kbb_fair_price({**base, "insights": {"cpo": True}})
    non_cpo = predict_kbb_fair_price({**base, "cpo": False})
    assert via_insights > non_cpo


def test_predict_kbb_no_mileage_still_returns_price():
    listing = {"make": "BMW", "model": "X3", "year": 2023, "trim": "xDrive30i"}
    assert predict_kbb_fair_price(listing) is not None


def test_predict_kbb_sanity_floor_and_ceiling():
    # Extremely old, cheap-base, extremely high-mileage car should floor at $10,000
    old = predict_kbb_fair_price({"make": "Hyundai", "model": "Tucson Hybrid", "year": 1990, "mileage": 500000})
    assert old == 10000
    # Extremely new / high-trim / low-mileage should still cap at $75,000
    new = predict_kbb_fair_price({
        "make": "BMW", "model": "X3", "year": _CURRENT_YEAR, "mileage": 0,
        "trim": "xDrive30i", "cpo": True, "powertrain": "phev", "drivetrain": "awd",
    })
    assert new <= 75000


def test_get_base_msrp_uses_cars_json_when_available():
    base_msrp, trim_delta = _get_base_msrp("BMW", "X3", 2023, "xDrive30i")
    assert base_msrp > 0
    assert trim_delta >= 0


def test_get_base_msrp_falls_back_for_unknown_vehicle():
    base_msrp, trim_delta = _get_base_msrp("Toyota", "Camry", 2023, "LE")
    assert base_msrp == 35000.0
    assert trim_delta == 0.0


def test_get_base_msrp_fallback_uses_model_keyword_buckets():
    base_msrp, _ = _get_base_msrp("Toyota", "Nonexistent Sedan", 2023, None)
    assert base_msrp == 35000.0  # no keyword match -> default fallback

    base_msrp_x3, _ = _get_base_msrp("Toyota", "some x3 clone", 2023, None)
    assert base_msrp_x3 == 47000.0  # "x3" keyword bucket


def test_update_kbb_market_cache_upserts_medians():
    rows = [
        {"make": "bmw", "model": "x3", "year": 2023, "mileage": 15000, "price": 40000,
         "trim": "xDrive30i", "drivetrain": "awd", "raw": {}},
        {"make": "bmw", "model": "x3", "year": 2023, "mileage": 16000, "price": 41000,
         "trim": "xDrive30i", "drivetrain": "awd", "raw": {}},
    ]
    with patch.object(kbb_service, "fetch_all", return_value=rows), \
         patch.object(kbb_service, "execute_values") as mock_ev:
        count = update_kbb_market_cache()

    assert count == 1  # both rows fall in the same (make, model, year, bucket) group
    mock_ev.assert_called_once()


def test_update_kbb_market_cache_skips_unpricable_rows():
    rows = [{"make": None, "model": None, "year": None, "mileage": 1000, "price": 1, "trim": None, "drivetrain": None, "raw": {}}]
    with patch.object(kbb_service, "fetch_all", return_value=rows), \
         patch.object(kbb_service, "execute_values") as mock_ev:
        count = update_kbb_market_cache()
    assert count == 0
    mock_ev.assert_not_called()


def test_update_kbb_market_cache_no_rows():
    with patch.object(kbb_service, "fetch_all", return_value=[]), \
         patch.object(kbb_service, "execute_values") as mock_ev:
        count = update_kbb_market_cache()
    assert count == 0
    mock_ev.assert_not_called()
