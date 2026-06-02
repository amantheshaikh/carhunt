import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from tools.scoring import detect_drivetrain, detect_powertrain, score_listing


# ── detect_drivetrain ──────────────────────────────────────────────────────────

def test_drivetrain_awd_keyword():
    assert detect_drivetrain("SEL Convenience AWD", "Tucson Hybrid") == "awd"

def test_drivetrain_4motion():
    assert detect_drivetrain("SEL R-Line 4Motion", "Tiguan") == "awd"

def test_drivetrain_4wd():
    assert detect_drivetrain("Tundra 4WD", None) == "awd"

def test_drivetrain_all_wheel():
    assert detect_drivetrain(None, None, "All-wheel drive") == "awd"

def test_drivetrain_fwd_keyword():
    assert detect_drivetrain("SE FWD", "Tiguan") == "fwd"

def test_drivetrain_front_wheel():
    assert detect_drivetrain(None, None, "Front-wheel drive only") == "fwd"

def test_drivetrain_unknown_when_no_keyword():
    # SE R-Line Black has no drivetrain keyword
    assert detect_drivetrain("SE R-Line Black", "Tiguan") == "unknown"

def test_drivetrain_none_inputs():
    assert detect_drivetrain(None, None) == "unknown"


# ── detect_powertrain ──────────────────────────────────────────────────────────

def test_powertrain_hybrid_from_model():
    assert detect_powertrain("SEL Convenience", "Tucson Hybrid") == "hybrid"

def test_powertrain_hybrid_from_trim():
    assert detect_powertrain("Hybrid Limited", None) == "hybrid"

def test_powertrain_phev():
    assert detect_powertrain("Plug-In Hybrid SEL", None) == "phev"

def test_powertrain_electric():
    assert detect_powertrain("IONIQ 6 SEL", None) == "electric"

def test_powertrain_gas():
    assert detect_powertrain("SE R-Line Black", "Tiguan") == "gas"

def test_powertrain_none_inputs():
    assert detect_powertrain(None, None) == "gas"


# ── score_listing ──────────────────────────────────────────────────────────────

def test_score_at_fair_value():
    assert score_listing(30_000, 30_000) == 50.0

def test_score_great_deal():
    # 20% below fair value → ≥ 90
    assert score_listing(24_000, 30_000) >= 90

def test_score_overpriced():
    # 20% above fair value → ≤ 10
    assert score_listing(36_000, 30_000) <= 10

def test_score_slightly_below():
    # 10% below market with steepness=25 → sigmoid(0.1*25) ≈ 92
    score = score_listing(27_000, 30_000)
    assert 88 <= score <= 95

def test_score_none_when_no_ml_price():
    assert score_listing(30_000, None) is None

def test_score_none_when_ml_price_zero():
    assert score_listing(30_000, 0) is None

def test_score_is_float_in_range():
    score = score_listing(28_000, 30_000)
    assert isinstance(score, float)
    assert 0 <= score <= 100
