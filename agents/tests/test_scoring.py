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

def test_powertrain_t8_boundary():
    assert detect_powertrain("T8", None) == "phev"
    assert detect_powertrain("T8 Ultimate", None) == "phev"
    assert detect_powertrain("Recharge T8", None) == "phev"

def test_powertrain_ev_boundary():
    assert detect_powertrain("EV", None) == "electric"
    assert detect_powertrain("EV Wind", None) == "electric"
    assert detect_powertrain("Niro EV", None) == "electric"

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
    # $3k below on $30k car, residual_std=1500 → z=2 → sigmoid(2*0.94) ≈ 87
    score = score_listing(27_000, 30_000, residual_std=1500)
    assert 84 <= score <= 90

def test_score_none_when_no_ml_price():
    assert score_listing(30_000, None) is None

def test_score_none_when_ml_price_zero():
    assert score_listing(30_000, 0) is None

def test_score_is_float_in_range():
    score = score_listing(28_000, 30_000)
    assert isinstance(score, float)
    assert 0 <= score <= 100

def test_score_with_residual_std_cap():
    # residual_std = 1000 (> 600)
    score = score_listing(27_000, 30_000, 1000.0)
    assert score is not None
    assert isinstance(score, float)
    assert score == 94.4  # round(100.0 / (1.0 + math.exp(-3.0 * 0.94)), 1) -> 94.4
    
    # residual_std = 500 (< 600), should be capped at 600.0
    score_500 = score_listing(27_000, 30_000, 500.0)
    score_600 = score_listing(27_000, 30_000, 600.0)
    assert score_500 == score_600
    assert score_500 == 99.1  # round(100.0 / (1.0 + math.exp(-5.0 * 0.94)), 1) -> 99.1

def test_drivetrain_extra_keywords():
    assert detect_drivetrain("X3 M40i", None) == "awd"
    assert detect_drivetrain("X1 M35i", None) == "awd"
    assert detect_drivetrain("quattro Premium", None) == "awd"
    assert detect_drivetrain("GLC 4matic", None) == "awd"
    assert detect_drivetrain("XC60 T6 B6", None) == "awd"
    assert detect_drivetrain("XC60 eAWD", None) == "awd"
    
    assert detect_drivetrain("X3 sDrive30i", None) == "fwd"
    assert detect_drivetrain("XC40 B4", None) == "fwd"
    assert detect_drivetrain("3 Series RWD", None) == "fwd"
    assert detect_drivetrain("2WD SE", None) == "fwd"

def test_powertrain_extra_keywords():
    assert detect_powertrain("XC90 T8 Recharge", None) == "phev"
    assert detect_powertrain("plug in hybrid SE", None) == "phev"
    
    assert detect_powertrain("RX 350h", None) == "hybrid"
    assert detect_powertrain("RX 500h F Sport", None) == "hybrid"
    
    assert detect_powertrain("Model Y BEV", None) == "electric"


def test_score_listing_overflow_bounds():
    # Extreme overpriced values should not raise OverflowError, they should clip to 0.0
    assert score_listing(1_000_000, 30_000, 600.0) == 0.0
    assert score_listing(10_000_000, 30_000) == 0.0
    
    # Extreme underpriced values should not raise OverflowError, they should clip to 100.0
    assert score_listing(1, 30_000, 600.0) == 100.0
    assert score_listing(1, 30_000) == 100.0


