"""
Unit tests and sanity checks for Playwright, API, and helper scrapers.
"""
import pytest
from tools.scrapers import (
    _parse_cargurus_blade,
    _parse_history,
    _focus_listing_text,
)

def test_focus_listing_text_truncation_and_anchoring():
    text_short = "Vehicle description with no special keywords."
    assert _focus_listing_text(text_short, limit=100) == text_short

    text_match = "Header info... " + ("padding " * 100) + "Certified Pre-Owned vehicle package included."
    focused = _focus_listing_text(text_match, limit=500)
    assert "Certified Pre-Owned" in focused

    long_prefix = "Boilerplate text line\n" * 500
    long_text = long_prefix + "Certified Pre-Owned Package Details"
    focused_long = _focus_listing_text(long_text, limit=2000)
    assert "Certified Pre-Owned" in focused_long
    assert len(focused_long) <= 2000

def test_parse_cargurus_blade_cross_variant_rejection():
    # Gas Sportage on Sportage PHEV page -> rejected
    text = "2024 Kia Sportage EX AWD\nLearn more\nEX\n$29,900\n15,000 mi"
    href = "https://www.cargurus.com/details/100001"
    parsed = _parse_cargurus_blade(text, href, expected_model="Sportage PHEV", expected_make="Kia")
    assert parsed is None

    # Actual PHEV listing -> accepted
    text_phev = "2024 Kia Sportage PHEV EX AWD\nLearn more\nEX\n$34,900\n10,000 mi"
    parsed_phev = _parse_cargurus_blade(text_phev, href, expected_model="Sportage PHEV", expected_make="Kia")
    assert parsed_phev is not None
    assert parsed_phev["model"] == "Sportage PHEV"

def test_parse_history_mileage_vs_distance():
    # Should ignore "150 miles away" and pick actual mileage 24,500
    page_text = """
    Location: Wall Township, NJ (150 miles away from your location)
    Vehicle Details:
    Clean Title
    0 Accidents Reported
    2 Previous Owners
    Mileage: 24,500 miles
    All-Wheel Drive drivetrain
    """
    history = _parse_history(page_text)
    assert history["accident_count"] == 0
    assert history["title_status"] == "clean"
    assert history["owner_count"] == 2
    assert history["mileage"] == 24500
    assert history["drivetrain"] == "awd"

def test_parse_history_lemon_detection():
    page_text = """
    Vehicle History Report:
    Manufacturer Buyback / Lemon Title Reported on 01/2024.
    Odometer: 18,200
    """
    history = _parse_history(page_text)
    assert history["title_status"] == "lemon"
    assert history["mileage"] == 18200


def test_parse_history_salvage_title():
    # The salvage pattern requires "salvage title" (adjacent words)
    page_text = """
    Vehicle History: Salvage Title
    1 accidents reported
    Odometer: 62,000 miles
    """
    history = _parse_history(page_text)
    assert history["title_status"] == "salvage"
    assert history["accident_count"] == 1


def test_parse_history_rebuilt_title():
    # "rebuilt title" is captured under the salvage patterns (same status bucket)
    page_text = """
    Title: Rebuilt Title
    0 accidents reported
    Mileage: 44,500 miles
    """
    history = _parse_history(page_text)
    assert history["title_status"] == "salvage"  # rebuilt title maps to salvage status


def test_parse_history_fleet_vehicle():
    # Pattern: "fleet vehicle" or "fleet vehicle"
    page_text = """
    Vehicle Use: Fleet Vehicle
    0 accidents reported
    Odometer: 78,100 miles
    """
    history = _parse_history(page_text)
    assert history["title_status"] == "fleet"
    assert history["accident_count"] == 0


def test_parse_history_multiple_owners():
    # Accident pattern requires "accidents reported" — "no accidents reported" → 0
    page_text = """
    No accidents reported
    3 Previous Owners
    Mileage: 55,200 miles
    """
    history = _parse_history(page_text)
    assert history["title_status"] == "clean"
    assert history["owner_count"] == 3
    assert history["accident_count"] == 0


def test_parse_history_no_data():
    # With no matching patterns, title_status defaults to "clean" (not None)
    history = _parse_history("")
    assert history["accident_count"] is None
    assert history["title_status"] == "clean"  # default when no bad patterns found
    assert history["mileage"] is None
