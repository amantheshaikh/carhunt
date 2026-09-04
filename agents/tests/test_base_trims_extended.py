import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from tools.base_trims import (
    normalize_listing_model,
    split_xc60_model,
    canonical_trim,
    is_trim_allowed,
    is_trim_allowed_for_training,
    get_model_min_year,
    get_model_max_year,
    get_trim_min_year,
    get_trim_max_year,
    _find_vehicle,
)

def test_split_xc60_model_gas_vs_phev():
    # B5 / Gas / Mild Hybrid -> "XC60"
    assert split_xc60_model("Volvo", "XC60", "Plus B5 AWD") == "XC60"
    assert split_xc60_model("Volvo", "XC60", "Ultimate Dark Theme") == "XC60"

    # Recharge / T8 -> "XC60 Recharge"
    assert split_xc60_model("Volvo", "XC60", "Plus Recharge T8 AWD") == "XC60 Recharge"
    assert split_xc60_model("Volvo", "XC60", "T8 Core PHEV") == "XC60 Recharge"

def test_normalize_listing_model_canonical_casing():
    mk, mo = normalize_listing_model("HYUNDAI", "tucson hybrid", "Limited")
    assert mk == "Hyundai"
    assert mo == "Tucson Hybrid"

def test_normalize_listing_model_xc60_recharge_still_split_but_not_a_target():
    # XC60 Recharge is no longer a tracked vehicle (removed from cars.json), but
    # split_xc60_model still correctly separates it out so it fails the allowlist
    # check downstream instead of being silently mislabeled as gas XC60. Since it
    # has no cars.json entry, it falls through without canonical-casing.
    mk, mo = normalize_listing_model("volvo", "xc60", "Plus Recharge T8")
    assert mk == "volvo"
    assert mo == "XC60 Recharge"

def test_canonical_trim_bmw_token_reorder():
    # Pass 2 BMW token-reorder: "30 xDrive" -> "xDrive30i"
    assert canonical_trim("BMW", "X3", "30 xDrive") == "xDrive30i"
    assert canonical_trim("BMW", "X3", "xDrive30i AWD") == "xDrive30i"

def test_canonical_trim_whole_word_substring():
    # Pass 3: "B5 Plus Dark Theme AWD" -> "Plus"
    assert canonical_trim("Volvo", "XC40", "B5 Plus Dark Theme AWD") == "Plus"
    assert canonical_trim("Volkswagen", "Tiguan", "SE R-Line Black 4Motion") == "SE R-Line Black"

def test_is_trim_allowed_for_training_broad_trims():
    # Training trims allow broader base tiers for depreciation curve modeling
    # Genesis GV70: 2.5T Standard and 2.5T Select are in training_trims (pass engine_filter)
    assert is_trim_allowed_for_training("Genesis", "GV70", "2.5T Standard") is True
    assert is_trim_allowed_for_training("Genesis", "GV70", "2.5T Select") is True

    # Tucson Hybrid: Blue and SEL are in training_trims
    assert is_trim_allowed_for_training("Hyundai", "Tucson Hybrid", "Blue") is True
    assert is_trim_allowed_for_training("Hyundai", "Tucson Hybrid", "SEL") is True

def test_year_constraint_helpers():
    # Model level year bounds
    assert get_model_min_year("Volkswagen", "Tiguan") == 2025
    assert get_model_max_year("Volkswagen", "Tiguan") == 2025

    assert get_model_min_year("Genesis", "GV70") == 2023
    assert get_model_max_year("Genesis", "GV70") == 2025

    # Trim level year bounds (e.g. BMW X3 sDrive30i max_year is 2024)
    assert get_trim_max_year("BMW", "X3", "sDrive30i") == 2024


def test_canonical_trim_engine_prefix_stripped():
    # _normalize_trim strips engine prefix before matching, so Pass 1 works on the stripped form
    # "350h Premium" → strip "350h " → "premium" → matches NX 350h allowed trim "Premium"
    assert canonical_trim("Lexus", "NX 350h", "350h Premium") == "Premium"
    assert canonical_trim("Lexus", "NX 350h", "350h F SPORT") == "F SPORT"
    # "2.5T Standard" → strip "2.5T " → "standard" → matches "Standard" in GV70 trims
    assert canonical_trim("Genesis", "GV70", "2.5T Standard") == "Standard"
    assert canonical_trim("Genesis", "GV70", "2.5T Advanced Plus") == "Advanced Plus"


def test_is_trim_allowed_gv70_engine_filter():
    # GV70 has engine_filter: ["2.5T"] — 3.5T variants must be rejected regardless of trim name
    assert is_trim_allowed("Genesis", "GV70", "3.5T Sport Prestige") is False
    assert is_trim_allowed("Genesis", "GV70", "2.5T Advanced") is True
    assert is_trim_allowed("Genesis", "GV70", "2.5T Sport Prestige") is True
    # No engine qualifier at all → rejected (can't confirm it's 2.5T)
    assert is_trim_allowed("Genesis", "GV70", "Advanced") is False


def test_is_trim_allowed_sportage_hybrid_trims():
    assert is_trim_allowed("Kia", "Sportage Hybrid", "EX") is True
    # LX is not in the allowed list
    assert is_trim_allowed("Kia", "Sportage Hybrid", "LX") is False


def test_is_trim_allowed_sportage_phev_untracked_allows_any_trim():
    # Sportage PHEV was removed as a target vehicle (no cars.json entry) -> _find_vehicle
    # returns None, so is_trim_allowed can't filter what it doesn't know and allows everything.
    assert is_trim_allowed("Kia", "Sportage PHEV", "EX Premium") is True
    assert is_trim_allowed("Kia", "Sportage PHEV", "LX") is True


def test_canonical_trim_volvo_b5_b4_prefix():
    # Volvo XC40: "B4 Plus" → strip "B4 " (but B4 isn't matched by _ENGINE_PREFIXES regex)
    # actual behavior: whole-word substring finds "Plus" in "b4 plus"
    assert canonical_trim("Volvo", "XC40", "B4 Plus") == "Plus"
    assert canonical_trim("Volvo", "XC40", "B5 Ultimate Dark Theme") == "Ultimate"


def test_split_xc60_model_no_trim():
    # When trim is None/empty, default to gas ("XC60") — can't determine PHEV without trim text
    assert split_xc60_model("Volvo", "XC60", None) == "XC60"
    assert split_xc60_model("Volvo", "XC60", "") == "XC60"


def test_normalize_listing_model_xc60_recharge_via_trim():
    mk, mo = normalize_listing_model("Volvo", "XC60", "Recharge T8 Ultimate")
    assert mk == "Volvo"
    assert mo == "XC60 Recharge"

    mk, mo = normalize_listing_model("Volvo", "XC60", "B5 Plus AWD")
    assert mk == "Volvo"
    assert mo == "XC60"
