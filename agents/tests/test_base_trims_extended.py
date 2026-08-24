import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from tools.base_trims import (
    normalize_listing_model,
    split_xc60_model,
    canonical_trim,
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
    # Test case-insensitivity & XC60 split
    mk, mo = normalize_listing_model("volvo", "xc60", "Plus Recharge T8")
    assert mk == "Volvo"
    assert mo == "XC60 Recharge"

    mk, mo = normalize_listing_model("HYUNDAI", "tucson hybrid", "Limited")
    assert mk == "Hyundai"
    assert mo == "Tucson Hybrid"

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
