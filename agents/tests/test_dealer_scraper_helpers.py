import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from tools.dealer_scraper import (
    _build_dealer_com_url,
    _build_dealeron_url,
    _parse_title,
    _llm_model_valid_for_title,
    _normalize_overfuel,
    _normalize_cosmos_vehicle,
    _normalize_carscommerce,
)

def test_build_dealer_com_url():
    url = "https://www.rickcasehyundai.com/used-inventory/index.htm"
    makes = ["Hyundai"]
    filtered = _build_dealer_com_url(url, makes, min_year=2024, max_year=2025)
    assert "make=Hyundai" in filtered
    assert "year=2024" in filtered
    assert "year=2025" in filtered

def test_build_dealeron_url():
    url = "https://www.edvoyleskia.com/searchused.aspx"
    makes = ["Kia"]
    filtered = _build_dealeron_url(url, makes, min_year=2023, max_year=2025)
    assert "yearrange=2023-2025" in filtered
    assert "ModelAndTrim=" in filtered

def test_parse_title_clean_strings():
    year, make, model, trim = _parse_title("Certified Pre-Owned 2024 Hyundai Tucson Hybrid SEL Convenience")
    assert year == "2024"
    assert make == "Hyundai"
    assert model == "Tucson Hybrid"
    assert trim == "SEL Convenience"

    year, make, model, trim = _parse_title("Used 2023 BMW X1 xDrive28i")
    assert year == "2023"
    assert make == "BMW"
    assert model == "X1"
    assert trim == "xDrive28i"

def test_llm_model_valid_for_title():
    # Powertrain qualifier check: 'Tucson Hybrid' model requires 'hybrid' in title
    assert _llm_model_valid_for_title("Tucson Hybrid", "2024 Hyundai Tucson Hybrid SEL") is True
    assert _llm_model_valid_for_title("Tucson Hybrid", "2024 Hyundai Tucson SEL") is False  # Missing 'hybrid'

    # Non-powertrain words check: 'GV70' model should not match 'GV80' title
    assert _llm_model_valid_for_title("GV70", "2024 Genesis GV70 2.5T Advanced") is True
    assert _llm_model_valid_for_title("GV70", "2024 Genesis GV80 3.5T") is False

def test_normalize_overfuel():
    raw_item = {
        "vin": "5NPE24AF8RH123456",
        "make": "Hyundai",
        "model": "Tucson Hybrid",
        "trim": "SEL Convenience",
        "year": 2025,
        "price": "34990.00",
        "mileage": 8500,
        "url": "/used/Hyundai/2025-Tucson-Hybrid-12345.htm",
        "dealer": {"name": "ALM Athens", "phone": "706-555-0199"},
    }
    listing = _normalize_overfuel(raw_item, "Hyundai", "https://www.almcars.com")
    assert listing is not None
    assert listing["vin"] == "5NPE24AF8RH123456"
    assert listing["make"] == "Hyundai"
    assert listing["model"] == "Tucson Hybrid"
    assert listing["trim"] == "SEL Convenience"
    assert listing["price"] == 34990
    assert listing["mileage"] == 8500
    assert listing["dealer_name"] == "ALM Athens"

def test_normalize_cosmos_vehicle():
    card = {
        "VehicleVin": "KNDPZ4A20R7123456",
        "VehicleMake": "Kia",
        "VehicleModel": "Sportage Hybrid",
        "VehicleTrim": "EX",
        "VehicleYear": 2024,
        "TaggingPrice": "31500",
        "Mileage": "12,400 miles",
        "VehicleDetailUrl": "/vehicle-details/used-2024-kia-sportage-hybrid-ex.htm",
        "VehicleCondition": "Used",
    }
    listing = _normalize_cosmos_vehicle(card, "Kia", "https://www.edvoyleskia.com")
    assert listing is not None
    assert listing["vin"] == "KNDPZ4A20R7123456"
    assert listing["make"] == "Kia"
    assert listing["model"] == "Sportage Hybrid"
    assert listing["trim"] == "EX"
    assert listing["price"] == 31500
    assert listing["mileage"] == 12400


def test_normalize_carscommerce():
    # Standard pre-owned vehicle with nested pricing and media
    v = {
        "vin": "WBXYJ1C04N5U12345",
        "make": "BMW",
        "model": "X3",
        "trim": "xDrive30i",
        "year": 2023,
        "mileage": 28450,
        "pricing": {"price": "38900"},
        "media": {"photos": [{"url_full": "https://cdn.example.com/photo1.jpg"}]},
        "vdp_url": "https://www.athensbmw.com/vehicle/WBXYJ1C04N5U12345/",
    }
    listing = _normalize_carscommerce(v, "BMW", "https://www.athensbmw.com")
    assert listing is not None
    assert listing["vin"] == "WBXYJ1C04N5U12345"
    assert listing["make"] == "BMW"
    assert listing["model"] == "X3"
    assert listing["trim"] == "xDrive30i"
    assert listing["year"] == 2023
    assert listing["price"] == 38900
    assert listing["mileage"] == 28450
    assert listing["listing_url"] == "https://www.athensbmw.com/vehicle/WBXYJ1C04N5U12345/"


def test_normalize_carscommerce_fallback_price_fields():
    # Falls back through our_price → internet_price → msrp when price is absent
    v = {
        "vin": "KNDPZ4A20R7654321",
        "make": "Kia",
        "model": "Sportage Hybrid",
        "trim": "EX",
        "year": 2024,
        "mileage": 11200,
        "pricing": {"our_price": "29500.00"},
        "media": {},
        "vdp_url": "",
    }
    listing = _normalize_carscommerce(v, "Kia", "https://www.sonskia.com")
    assert listing is not None
    assert listing["price"] == 29500


def test_normalize_carscommerce_no_price_returns_none():
    v = {
        "vin": "5NPEF4JA0MH012345",
        "make": "Hyundai",
        "model": "Sonata",
        "trim": "SEL",
        "year": 2021,
        "mileage": 40000,
        "pricing": {},
        "media": {},
    }
    assert _normalize_carscommerce(v, "Hyundai", "https://www.example.com") is None
