import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sourcing_agent import _is_valid, _get_known_fingerprints

def test_is_valid_clean_listing():
    listing = {
        "make": "Hyundai",
        "model": "Tucson Hybrid",
        "trim": "SEL Convenience",
        "year": 2025,
        "price": 32000,
        "mileage": 10000,
        "title_status": "clean",
        "accident_count": 0,
        "dealer_name": "Jim Ellis Hyundai, GA",
        "distance_miles": 15,
        "condition": "used",
    }
    assert _is_valid(listing, min_year=2023, allowed_makes=["Hyundai"]) is True

def test_is_valid_bad_title_status():
    base = {
        "make": "Hyundai", "model": "Tucson Hybrid", "trim": "SEL Convenience",
        "year": 2025, "price": 32000, "mileage": 10000, "accident_count": 0,
    }
    for bad_status in ["salvage", "rebuilt", "flood", "lemon", "fleet"]:
        listing = {**base, "title_status": bad_status}
        assert _is_valid(listing, min_year=2023, allowed_makes=["Hyundai"]) is False

def test_is_valid_accident_count():
    listing = {
        "make": "Hyundai", "model": "Tucson Hybrid", "trim": "SEL Convenience",
        "year": 2025, "price": 32000, "mileage": 10000, "title_status": "clean",
        "accident_count": 1,
    }
    assert _is_valid(listing, min_year=2023, allowed_makes=["Hyundai"]) is False

def test_is_valid_price_bounds():
    base = {
        "make": "Hyundai", "model": "Tucson Hybrid", "trim": "SEL Convenience",
        "year": 2025, "mileage": 10000, "title_status": "clean", "accident_count": 0,
    }
    # Too low (< $8000)
    assert _is_valid({**base, "price": 5000}, min_year=2023, allowed_makes=["Hyundai"]) is False
    # Exceeds max_price ($45000)
    assert _is_valid({**base, "price": 50000}, min_year=2023, allowed_makes=["Hyundai"], max_price=45000) is False
    # Valid price
    assert _is_valid({**base, "price": 35000}, min_year=2023, allowed_makes=["Hyundai"], max_price=45000) is True

def test_is_valid_excessive_mileage_rate():
    # 2025 car with 30,000 miles (assuming age 1-2 yrs) -> rate > 14k mi/yr
    listing = {
        "make": "Hyundai", "model": "Tucson Hybrid", "trim": "SEL Convenience",
        "year": 2025, "price": 30000, "mileage": 35000, "title_status": "clean",
        "accident_count": 0, "condition": "used",
    }
    assert _is_valid(listing, min_year=2023, allowed_makes=["Hyundai"]) is False

def test_is_valid_distance_and_state_filtering():
    base = {
        "make": "Hyundai", "model": "Tucson Hybrid", "trim": "SEL Convenience",
        "year": 2025, "price": 32000, "mileage": 10000, "title_status": "clean", "accident_count": 0,
    }
    # Distance within max_distance
    assert _is_valid({**base, "distance_miles": 80}, min_year=2023, allowed_makes=["Hyundai"], max_distance=100) is True
    # Distance exceeds max_distance
    assert _is_valid({**base, "distance_miles": 150}, min_year=2023, allowed_makes=["Hyundai"], max_distance=100) is False

    # Store transfer listing with no distance -> reject
    assert _is_valid({**base, "distance_miles": None, "dealer_name": "Store Transfer to Roswell"}, min_year=2023, allowed_makes=["Hyundai"], max_distance=100) is False

    # Home delivery listing from non-GA state -> reject
    assert _is_valid({**base, "distance_miles": None, "dealer_name": "Wall Township, NJ"}, min_year=2023, allowed_makes=["Hyundai"], max_distance=100) is False

    # Home delivery listing from GA -> accept
    assert _is_valid({**base, "distance_miles": None, "dealer_name": "Marietta, GA"}, min_year=2023, allowed_makes=["Hyundai"], max_distance=100) is True

def test_is_valid_model_and_trim_year_constraints():
    base = {
        "make": "Volkswagen", "model": "Tiguan", "trim": "SE R-Line Black",
        "price": 30000, "mileage": 5000, "title_status": "clean", "accident_count": 0,
    }
    # Tiguan min_year is 2025
    assert _is_valid({**base, "year": 2024}, min_year=2023, allowed_makes=["Volkswagen"]) is False
    assert _is_valid({**base, "year": 2025}, min_year=2023, allowed_makes=["Volkswagen"]) is True

def test_is_valid_cross_model_trim_contamination():
    # "SEL Convenience" belongs to Tucson Hybrid, not Sportage Hybrid
    listing = {
        "make": "Kia", "model": "Sportage Hybrid", "trim": "SEL Convenience",
        "year": 2024, "price": 30000, "mileage": 10000, "title_status": "clean", "accident_count": 0,
    }
    assert _is_valid(listing, min_year=2023, allowed_makes=["Kia"]) is False

def test_touch_existing_price_drop_reactivation():
    from sourcing_agent import _touch_existing
    from unittest.mock import patch, MagicMock

    with patch("sourcing_agent.get_conn") as mock_get_conn:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_conn.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # Price drop: new_price (30000) < old_price (33000) -> price_dropped is True
        _touch_existing("cargurus", "ext123", 30000, 33000)
        executed_sqls = [call[0][0] for call in mock_cur.execute.call_args_list]
        assert any("status = 'dismissed'" in sql for sql in executed_sqls)

        # Price increase: new_price (35000) > old_price (33000) -> price_dropped is False
        mock_cur.reset_mock()
        _touch_existing("cargurus", "ext123", 35000, 33000)
        executed_sqls_inc = [call[0][0] for call in mock_cur.execute.call_args_list]
        assert not any("status = 'dismissed'" in sql for sql in executed_sqls_inc)


def test_is_valid_no_price():
    listing = {
        "make": "Hyundai", "model": "Tucson Hybrid", "trim": "SEL Convenience",
        "year": 2025, "mileage": 10000, "title_status": "clean", "accident_count": 0,
        "price": None,
    }
    assert _is_valid(listing, min_year=2023, allowed_makes=["Hyundai"]) is False


def test_is_valid_no_make():
    listing = {
        "make": None, "model": "Tucson Hybrid", "trim": "SEL Convenience",
        "year": 2025, "price": 32000, "mileage": 10000, "title_status": "clean", "accident_count": 0,
    }
    assert _is_valid(listing, min_year=2023, allowed_makes=["Hyundai"]) is False


def test_is_valid_cpo_bypasses_mileage_rate():
    # CPO vehicles can have higher mileage — rate validation should not apply
    listing = {
        "make": "BMW", "model": "X3", "trim": "xDrive30i",
        "year": 2023, "price": 38000, "mileage": 40000,
        "title_status": "clean", "accident_count": 0, "condition": "cpo",
    }
    # 40k miles on a 3-year-old car = ~13k mi/yr — valid, should not be rejected
    assert _is_valid(listing, min_year=2023, allowed_makes=["BMW"]) is True


def test_is_valid_max_year_constraint():
    base = {
        "make": "Volkswagen", "model": "Tiguan", "trim": "SE R-Line Black",
        "price": 30000, "mileage": 5000, "title_status": "clean", "accident_count": 0,
    }
    # Tiguan max_year=2025; 2026 should be rejected
    assert _is_valid({**base, "year": 2026}, min_year=2023, allowed_makes=["Volkswagen"]) is False
    assert _is_valid({**base, "year": 2025}, min_year=2023, allowed_makes=["Volkswagen"]) is True


def test_is_valid_flood_title():
    listing = {
        "make": "Kia", "model": "Sportage Hybrid", "trim": "EX",
        "year": 2024, "price": 28000, "mileage": 15000, "accident_count": 0,
        "title_status": "flood",
    }
    assert _is_valid(listing, min_year=2023, allowed_makes=["Kia"]) is False
