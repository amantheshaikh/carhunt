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
    _cpo_urls_for,
    _int,
)

def test_cpo_urls_for():
    urls = _cpo_urls_for("https://www.hennessyauto.com/used-inventory/index.htm")
    assert "https://www.hennessyauto.com/certified-inventory/index.htm" in urls
    assert "https://www.hennessyauto.com/certified-pre-owned/" in urls
    assert "https://www.hennessyauto.com/certified/" in urls
    assert _cpo_urls_for("https://www.hennessyauto.com/certified-inventory/index.htm") == []

def test_build_dealer_com_url():
    url = "https://www.rickcasehyundai.com/used-inventory/index.htm"
    makes = ["Hyundai"]
    filtered = _build_dealer_com_url(url, makes, min_year=2024, max_year=2025)
    assert "make=Hyundai" in filtered
    # Year params must NOT be added — DDC SSR breaks with multiple year= values
    assert "year=" not in filtered

def test_build_dealer_com_url_no_year_params():
    # Even with a wide year range, no year= params should appear
    url = "https://www.nalleybmw.com/used-inventory/index.htm"
    filtered = _build_dealer_com_url(url, ["BMW"], min_year=2022, max_year=2026)
    assert "year=" not in filtered
    assert "make=BMW" in filtered

def test_build_dealer_com_url_strips_existing_year_in_source_url():
    # Existing year= params in the source URL must be stripped to prevent DDC SSR breakage
    url = "https://www.example.com/used-inventory/index.htm?year=2020&year=2021&sortBy=price"
    filtered = _build_dealer_com_url(url, ["BMW"], min_year=2022, max_year=2025)
    assert "year=2020" not in filtered
    assert "year=2021" not in filtered
    assert "sortBy=price" in filtered  # non-year params preserved

def test_build_dealer_com_url_non_ddc_passthrough():
    # DealerInspire URLs (no index.htm) must pass through unchanged
    di_url = "https://www.sonskia.com/used-vehicles/"
    assert _build_dealer_com_url(di_url, ["Kia"], min_year=2023, max_year=2025) == di_url

    # CDK path required — a generic index.htm without /used-inventory/ must pass through
    generic_url = "https://www.example.com/index.htm"
    assert _build_dealer_com_url(generic_url, ["BMW"], min_year=2022, max_year=2025) == generic_url

def test_build_dealer_com_url_multi_make():
    url = "https://www.jimellis.com/used-inventory/index.htm"
    makes = ["BMW", "Lexus", "Kia"]
    filtered = _build_dealer_com_url(url, makes, min_year=2022, max_year=2026)
    assert "make=BMW" in filtered
    assert "make=Lexus" in filtered
    assert "make=Kia" in filtered
    assert "year=" not in filtered

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


def test_build_dealeron_url_amazingluxurycars_passthrough():
    # amazingluxurycars.com must pass through unchanged — its URL structure is not DealerOn
    url = "https://www.amazingluxurycars.com/inventory/used/?_pageSize=10"
    result = _build_dealeron_url(url, ["BMW"], min_year=2022, max_year=2026)
    assert result == url


def test_normalize_cosmos_vehicle_no_price():
    # TaggingPrice=0, VehicleMsrp=0 → price must be None, not 0
    card = {
        "VehicleVin": "3VVRB7AX5RM060194",
        "VehicleMake": "Volkswagen",
        "VehicleModel": "Tiguan",
        "VehicleTrim": "S",
        "VehicleYear": 2024,
        "TaggingPrice": 0,
        "VehicleMsrp": 0.0,
        "Mileage": "60,932 mi",
        "VehicleDetailUrl": "/used/2024-vw-tiguan.htm",
        "VehicleCondition": "Used",
    }
    listing = _normalize_cosmos_vehicle(card, "Volkswagen", "https://www.kiaatlantasouth.com")
    assert listing is not None
    assert listing["price"] is None  # 0 must be treated as missing, not $0

def test_normalize_cosmos_vehicle_msrp_fallback():
    # TaggingPrice absent but VehicleMsrp present → use Msrp
    card = {
        "VehicleVin": "KNDPZ4A20R7999999",
        "VehicleMake": "Kia",
        "VehicleModel": "Sportage Hybrid",
        "VehicleTrim": "EX",
        "VehicleYear": 2024,
        "VehicleMsrp": 31500.0,
        "Mileage": "5,000 miles",
        "VehicleDetailUrl": "/vehicle/used-kia-sportage.htm",
        "VehicleCondition": "Used",
    }
    listing = _normalize_cosmos_vehicle(card, "Kia", "https://www.example.com")
    assert listing["price"] == 31500

def test_normalize_cosmos_vehicle_cpo():
    card = {
        "VehicleVin": "KNDPZ4A20R7888888",
        "VehicleMake": "Kia",
        "VehicleModel": "Sportage Hybrid",
        "VehicleTrim": "SX Prestige",
        "VehicleYear": 2024,
        "TaggingPrice": 35000,
        "Mileage": "8,200 miles",
        "VehicleDetailUrl": "/vehicle/cpo-kia-sportage.htm",
        "VehicleCondition": "Certified Pre-Owned",
        "VehicleCpo": True,
    }
    listing = _normalize_cosmos_vehicle(card, "Kia", "https://www.example.com")
    assert listing["condition"] == "cpo"


def test_parse_title_used_prefix_stripped():
    # cobalt_dom card body text often starts with "Used 2025 BMW..."
    year, make, model, trim = _parse_title("Used 2025 BMW X3 xDrive30i")
    assert year == "2025"
    assert make == "BMW"
    assert model == "X3"
    assert trim == "xDrive30i"

def test_parse_title_preowned_cpo_prefix():
    year, make, model, trim = _parse_title("Pre-Owned 2024 Hyundai Tucson Hybrid SEL Convenience")
    assert year == "2024"
    assert make == "Hyundai"
    assert model == "Tucson Hybrid"
    assert trim == "SEL Convenience"

def test_parse_title_body_type_in_title():
    # cobalt_dom SSR cards often include body type at the end: "2026 Volkswagen Tiguan SE R-Line Black SUV"
    year, make, model, trim = _parse_title("2026 Volkswagen Tiguan SE R-Line Black SUV")
    assert year == "2026"
    assert make == "Volkswagen"
    # Model or trim should include SE R-Line Black; body type "SUV" may or may not appear
    assert model is not None
    assert "Tiguan" in model or "Tiguan" in (trim or "")


def test_llm_model_valid_powertrain_qualifier_both_ways():
    # Model with no powertrain qualifier ("GV70") must not require one in title
    assert _llm_model_valid_for_title("GV70", "2024 Genesis GV70 Advanced") is True
    assert _llm_model_valid_for_title("GV70", "2024 Genesis GV70 Sport Prestige") is True

def test_llm_model_valid_recharge_qualifier():
    # "XC60 Recharge" requires "recharge" in title
    assert _llm_model_valid_for_title("XC60 Recharge", "2024 Volvo XC60 Recharge T8 Plus") is True
    assert _llm_model_valid_for_title("XC60 Recharge", "2024 Volvo XC60 Plus AWD") is False


def test_int_decimal_string():
    # Cobalt JSON-LD prices sometimes come as "29195.0" — must not become 2919500
    assert _int("29195.0") == 29195
    assert _int("34990.00") == 34990
    assert _int("1,234.5") == 1234

def test_int_comma_separated():
    assert _int("28,500") == 28500

def test_int_none_and_empty():
    assert _int(None) is None
    assert _int("") is None
    assert _int("N/A") is None

def test_int_already_int():
    assert _int(35000) == 35000
