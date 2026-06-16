import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import tools.dealer_scraper as ds
from tools.dealer_scraper import _parse_title


def test_clean_title_no_llm(monkeypatch):
    """Clean standard titles should parse deterministically without calling LLM."""
    called = {"llm": False}
    def dummy_llm(title):
        called["llm"] = True
        return None
    monkeypatch.setattr(ds, "_parse_title_llm", dummy_llm)

    # "2025 Hyundai Tucson Hybrid Limited" matches target make/model/trim and is clean
    year, make, model, trim = _parse_title("2025 Hyundai Tucson Hybrid Limited")
    assert year == "2025"
    assert make == "Hyundai"
    assert model == "Tucson Hybrid"
    assert trim == "Limited"
    assert called["llm"] is False


def test_messy_title_fallback_llm(monkeypatch):
    """Messy title should fallback to LLM when model/make fails standard matching."""
    called = {"llm": False}
    def mock_llm(title):
        called["llm"] = True
        if "24 Sportage Hybrid" in title:
            return "2024", "Kia", "Sportage Hybrid", "EX"
        return None

    monkeypatch.setattr(ds, "_parse_title_llm", mock_llm)
    monkeypatch.setattr(ds, "_llm_title_cache", {})

    # "Hot Buy! 24 Sportage Hybrid" doesn't parse model correctly due to prefix and short year
    res = _parse_title("Hot Buy! 24 Sportage Hybrid")
    assert res == ("2024", "Kia", "Sportage Hybrid", "EX")
    assert called["llm"] is True


def test_title_cache_prevents_duplicate_calls(monkeypatch):
    """Cached title results should return immediately without repeating LLM call."""
    call_count = {"llm": 0}
    def mock_llm(title):
        call_count["llm"] += 1
        return "2025", "Genesis", "GV70", "Advanced"

    monkeypatch.setattr(ds, "_parse_title_llm", mock_llm)
    monkeypatch.setattr(ds, "_llm_title_cache", {})

    res1 = _parse_title("GV70 Messy Title A")
    res2 = _parse_title("GV70 Messy Title A")

    assert res1 == ("2025", "Genesis", "GV70", "Advanced")
    assert res2 == ("2025", "Genesis", "GV70", "Advanced")
    assert call_count["llm"] == 1  # only called once, second is served from cache
