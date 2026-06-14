import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from tools.price_model import _trim_premium, _is_cpo, _to_features


def test_trim_premium_matched():
    # Tucson Hybrid 2025:
    # "SEL": 33450, "SEL Convenience": 36465, "N Line": 38215, "Limited": 41295
    # Base is SEL (33450). SEL Convenience premium = 36465 - 33450 = 3015
    row = {
        "make": "Hyundai",
        "model": "Tucson Hybrid",
        "year": 2025,
        "trim": "SEL Convenience",
        "drivetrain": "awd"
    }
    assert _trim_premium(row) == 3015.0

    row_limited = {
        "make": "Hyundai",
        "model": "Tucson Hybrid",
        "year": 2025,
        "trim": "Limited",
        "drivetrain": "awd"
    }
    assert _trim_premium(row_limited) == 7845.0


def test_trim_premium_fallback():
    # If the trim is not found or config is missing, it should fall back to ordinal approximation
    # e.g., (ordinal - 1) * 2000.
    # Level for unmatched/missing is 2.5, so premium = (2.5 - 1.0) * 2000 = 3000.0
    row = {
        "make": "Hyundai",
        "model": "Tucson Hybrid",
        "year": 2025,
        "trim": "Nonexistent Trim Tier",
        "drivetrain": "awd"
    }
    assert _trim_premium(row) == 3000.0


def test_is_cpo_direct():
    assert _is_cpo({"cpo": True}) is True
    assert _is_cpo({"cpo": "True"}) is True
    assert _is_cpo({"cpo": "yes"}) is True
    assert _is_cpo({"cpo": False}) is False


def test_is_cpo_condition():
    assert _is_cpo({"condition": "cpo"}) is True
    assert _is_cpo({"condition": "used"}) is False


def test_is_cpo_insights():
    assert _is_cpo({"insights": {"cpo": True}}) is True
    assert _is_cpo({"insights": '{"cpo": true}'}) is True
    assert _is_cpo({"insights": {"cpo": False}}) is False


def test_is_cpo_keywords():
    assert _is_cpo({"trim": "Certified Pre-Owned", "raw": {"text": "some description"}}) is True
    assert _is_cpo({"trim": "SE", "raw": {"text": "This is a CPO vehicle with clean history"}}) is True
    # CarMax/Carvana certified should be ignored unless explicit CPO keywords present
    assert _is_cpo({"trim": "SE", "raw": {"text": "CarMax Certified quality check completed"}}) is False
    assert _is_cpo({"trim": "SE", "raw": {"text": "Carvana Certified vehicle"}}) is False
    assert _is_cpo({"trim": "SE", "raw": {"text": "Carvana Certified CPO vehicle"}}) is True


def test_to_features_shape_and_values():
    row = {
        "make": "Hyundai",
        "model": "Tucson Hybrid",
        "year": 2025,
        "trim": "SEL Convenience",
        "drivetrain": "awd",
        "powertrain": "hybrid",
        "mileage": 10000,
        "cpo": True
    }
    features = _to_features(row)
    # Features order: [base_msrp, year, mileage, trim_premium, drivetrain, powertrain, cpo]
    assert len(features) == 7
    assert features[0] == 33450.0  # base_msrp
    assert features[1] == 2025.0   # year
    assert features[3] == 3015.0   # trim premium
    assert features[4] == 1.0      # awd
    assert features[5] == 0.8      # hybrid
    assert features[6] == 1.0      # cpo
