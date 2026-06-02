import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from tools.base_trims import (
    _norm,
    is_trim_allowed,
    trim_belongs_to_different_model,
    get_allowed_models,
    get_allowed_makes,
)


# ── _norm ──────────────────────────────────────────────────────────────────────

def test_norm_strips_hyphens_and_spaces():
    assert _norm("SE R-Line Black") == "serlineblack"

def test_norm_lowercases():
    assert _norm("Tucson Hybrid") == "tucsonhybrid"

def test_norm_combined():
    assert _norm("X-Line AWD") == "xlineawd"


# ── is_trim_allowed — Tucson Hybrid ───────────────────────────────────────────

def test_tucson_sel_convenience_allowed():
    assert is_trim_allowed("Hyundai", "Tucson Hybrid", "SEL Convenience") is True

def test_tucson_limited_allowed():
    assert is_trim_allowed("Hyundai", "Tucson Hybrid", "Limited") is True

def test_tucson_limited_awd_allowed():
    # Prefix match: "limited awd" starts with "limited"
    assert is_trim_allowed("Hyundai", "Tucson Hybrid", "Limited AWD") is True

def test_tucson_n_line_allowed():
    assert is_trim_allowed("Hyundai", "Tucson Hybrid", "N Line") is True

def test_tucson_base_sel_rejected():
    assert is_trim_allowed("Hyundai", "Tucson Hybrid", "SEL") is False

def test_tucson_blue_rejected():
    assert is_trim_allowed("Hyundai", "Tucson Hybrid", "Blue") is False

def test_tucson_no_trim_passes():
    assert is_trim_allowed("Hyundai", "Tucson Hybrid", None) is True


# ── is_trim_allowed — Tiguan ──────────────────────────────────────────────────

def test_tiguan_se_rline_black_allowed():
    assert is_trim_allowed("Volkswagen", "Tiguan", "SE R-Line Black") is True

def test_tiguan_se_rline_black_fwd_allowed():
    # Prefix match: "se r line black fwd" starts with "se r line black"
    assert is_trim_allowed("Volkswagen", "Tiguan", "SE R-Line Black FWD") is True

def test_tiguan_sel_rline_allowed():
    assert is_trim_allowed("Volkswagen", "Tiguan", "SEL R-Line") is True

def test_tiguan_base_se_rejected():
    assert is_trim_allowed("Volkswagen", "Tiguan", "SE") is False

def test_tiguan_s_trim_rejected():
    assert is_trim_allowed("Volkswagen", "Tiguan", "S") is False


# ── is_trim_allowed — Sportage Hybrid ────────────────────────────────────────

def test_sportage_ex_allowed():
    assert is_trim_allowed("Kia", "Sportage Hybrid", "EX") is True

def test_sportage_x_line_allowed():
    assert is_trim_allowed("Kia", "Sportage Hybrid", "X-Line") is True

def test_sportage_sx_prestige_allowed():
    assert is_trim_allowed("Kia", "Sportage Hybrid", "SX Prestige") is True

def test_sportage_lx_rejected():
    assert is_trim_allowed("Kia", "Sportage Hybrid", "LX") is False


# ── trim_belongs_to_different_model ──────────────────────────────────────────

def test_tucson_trim_on_sportage_is_contamination():
    assert trim_belongs_to_different_model("Kia", "Sportage Hybrid", "SEL Convenience") is True

def test_tiguan_trim_on_sportage_is_contamination():
    assert trim_belongs_to_different_model("Kia", "Sportage Hybrid", "SEL R-Line") is True

def test_own_trim_not_contamination():
    assert trim_belongs_to_different_model("Hyundai", "Tucson Hybrid", "SEL Convenience") is False

def test_unknown_trim_not_contamination():
    assert trim_belongs_to_different_model("Hyundai", "Tucson Hybrid", "Sport") is False

def test_no_trim_not_contamination():
    assert trim_belongs_to_different_model("Kia", "Sportage Hybrid", None) is False


# ── model / make lists ────────────────────────────────────────────────────────

def test_get_allowed_models_has_all_targets():
    models = get_allowed_models()
    assert "Tucson Hybrid" in models
    assert "Sportage Hybrid" in models
    assert "Tiguan" in models

def test_get_allowed_makes():
    makes = get_allowed_makes()
    assert "Hyundai" in makes
    assert "Kia" in makes
    assert "Volkswagen" in makes
