import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from tools.base_trims import (
    _norm,
    is_trim_allowed,
    trim_belongs_to_different_model,
    get_allowed_models,
    get_allowed_makes,
    canonical_trim,
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

def test_tiguan_base_se_allowed():
    assert is_trim_allowed("Volkswagen", "Tiguan", "SE") is True

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
    assert trim_belongs_to_different_model("Hyundai", "Tucson Hybrid", "UnknownTrim") is False

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


# ── Volvo XC60 Split Logic ──────────────────────────────────────────────────

def test_volvo_xc60_recharge_split():
    def split_model(make: str, model: str, trim: str) -> str:
        mk, mo = make, model
        raw_trim = (trim or "").lower()
        if mk.lower() == "volvo" and mo.lower() in ("xc60", "xc60 recharge"):
            if "recharge" in raw_trim or "t8" in raw_trim:
                mo = "XC60 Recharge"
            else:
                mo = "XC60"
        return mo

    assert split_model("Volvo", "XC60", "B5 Plus Dark Theme FWD") == "XC60"
    assert split_model("Volvo", "XC60 Recharge", "B5 Plus Dark Theme FWD") == "XC60"
    assert split_model("Volvo", "XC60", "Recharge T8 Plus Dark Theme eAWD") == "XC60 Recharge"
    assert split_model("Volvo", "XC60 Recharge", "Recharge T8 Plus Dark Theme eAWD") == "XC60 Recharge"
    assert split_model("Volvo", "XC60", "Ultimate") == "XC60"
    assert split_model("Volvo", "XC60", "Ultimate Recharge") == "XC60 Recharge"
    assert split_model("Hyundai", "Tucson Hybrid", "Limited") == "Tucson Hybrid"


# ── canonical_trim for training_trims ─────────────────────────────────────────

def test_canonical_trim_training_only():
    assert canonical_trim("Hyundai", "Tucson Hybrid", "SEL FWD") == "SEL"
    assert canonical_trim("Kia", "Sportage Hybrid", "LX AWD") == "LX"
    assert canonical_trim("Volkswagen", "Tiguan", "S FWD") == "S"
    assert canonical_trim("Hyundai", "Tucson Hybrid", "SEL Convenience AWD") == "SEL Convenience"


# ── LLM trim fallback ─────────────────────────────────────────────────────────

import tools.base_trims as _bt


def _mock_llm(make, model, raw_trim, allowed_trims):
    """Fake LLM: maps hard-coded abbreviations to canonical names."""
    mapping = {
        "adv plus": "Advanced Plus",
        "prem plus": "Premium Plus",
        "prem+":     "Premium Plus",
        "limited awd pkg": "Limited",
        "sel conv": "SEL Convenience",
        "n-line":   "N Line",
    }
    return mapping.get(raw_trim.lower())


def test_llm_fallback_canonical_trim(monkeypatch):
    """canonical_trim should use LLM when all 3 regex passes fail."""
    monkeypatch.setattr(_bt, "_llm_canonical_trim", _mock_llm)
    monkeypatch.setattr(_bt, "_llm_trim_cache", {})
    assert canonical_trim("Genesis", "GV70", "ADV PLUS") == "Advanced Plus"
    assert canonical_trim("Mazda", "CX-50 Hybrid", "PREM PLUS") == "Premium Plus"


def test_llm_fallback_canonical_trim_for_accepted_listing(monkeypatch):
    """canonical_trim should map non-standard strings that already passed is_trim_allowed."""
    monkeypatch.setattr(_bt, "_llm_canonical_trim", _mock_llm)
    monkeypatch.setattr(_bt, "_llm_trim_cache", {})
    # "LIMITED AWD PKG" passes is_trim_allowed via prefix "limited" but gets wrong canonical
    # without LLM; mock maps it to "Limited"
    assert canonical_trim("Hyundai", "Tucson Hybrid", "LIMITED AWD PKG") == "Limited"


def test_llm_not_used_for_gating(monkeypatch):
    """is_trim_allowed must be regex-only — LLM false positives would corrupt sourcing."""
    call_count = {"n": 0}
    def _spy_llm(*a):
        call_count["n"] += 1
        return "SEL Convenience"

    monkeypatch.setattr(_bt, "_llm_canonical_trim", _spy_llm)
    # "SEL" should be rejected — it's a distinct lower trim, not "SEL Convenience"
    assert is_trim_allowed("Hyundai", "Tucson Hybrid", "SEL") is False
    assert call_count["n"] == 0  # LLM must not be called from is_trim_allowed


def test_llm_fallback_returns_none_for_unrecognized(monkeypatch):
    """canonical_trim falls back to raw trim when LLM returns None."""
    monkeypatch.setattr(_bt, "_llm_canonical_trim", lambda *a: None)
    monkeypatch.setattr(_bt, "_llm_trim_cache", {})
    assert canonical_trim("Hyundai", "Tucson Hybrid", "TOTALLY RANDOM TRIM XYZ") == "TOTALLY RANDOM TRIM XYZ"


def test_llm_result_validated_against_allowlist(monkeypatch):
    """canonical_trim must reject a hallucinated trim not in the allowed list."""
    monkeypatch.setattr(_bt, "_llm_canonical_trim", lambda *a: "Hallucinated Trim")
    monkeypatch.setattr(_bt, "_llm_trim_cache", {})
    result = canonical_trim("Hyundai", "Tucson Hybrid", "SOME RAW TRIM")
    assert result == "SOME RAW TRIM"  # raw fallback, not the hallucinated value


def test_llm_cache_prevents_duplicate_calls(monkeypatch):
    """Second call with the same (make, model, trim) must not invoke the LLM again."""
    call_count = {"n": 0}
    def _counting_llm(make, model, raw_trim, allowed_trims):
        call_count["n"] += 1
        return "Advanced Plus" if raw_trim.lower() == "adv plus" else None

    monkeypatch.setattr(_bt, "_llm_canonical_trim", _counting_llm)
    monkeypatch.setattr(_bt, "_llm_trim_cache", {})
    canonical_trim("Genesis", "GV70", "ADV PLUS")
    canonical_trim("Genesis", "GV70", "ADV PLUS")
    assert call_count["n"] == 1  # second call served from _llm_trim_cache

