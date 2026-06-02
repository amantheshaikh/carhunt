"""
Vehicle config loader. Single source of truth is agents/cars.json.
"""
import json
import os
import re

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "cars.json")

# Load once at import time — cars.json never changes during a run
with open(_CONFIG_PATH) as _f:
    _VEHICLES: list[dict] = json.load(_f)["vehicles"]


def _norm(s: str) -> str:
    return s.lower().replace("-", "").replace(" ", "")


def _find_vehicle(make: str, model: str) -> dict | None:
    """Return the cars.json entry for this make/model, or None if not found."""
    make_norm  = _norm(make)
    model_norm = _norm(model)
    for v in _VEHICLES:
        if _norm(v["make"]) == make_norm and _norm(v["model"]) == model_norm:
            return v
    return None


def get_allowed_makes() -> list[str]:
    return list({v["make"] for v in _VEHICLES})


def get_allowed_models() -> list[str]:
    return [v["model"] for v in _VEHICLES]


def get_vehicles() -> list[tuple[str, str]]:
    """Return [(make, model)] pairs for all target vehicles."""
    return [(v["make"], v["model"]) for v in _VEHICLES]


_ENGINE_PREFIXES = re.compile(r"^(?:\d+\.\d+[tTlLiIdDhH]|\d{2,4}[hHe])\s+", re.IGNORECASE)


def _normalize_trim(t: str) -> str:
    """Normalize trim for comparison: lowercase, hyphens→spaces, strip engine prefix."""
    t = t.lower().strip().replace("-", " ")
    t = _ENGINE_PREFIXES.sub("", t)  # strip "2.0T ", "3.5L ", etc.
    return t


def is_trim_allowed(make: str, model: str, trim: str | None) -> bool:
    """Return True if the trim is acceptable for this make/model.
    If no trims allowlist is configured, all trims pass.
    A listing with no trim info also passes (can't filter what we don't know).
    """
    if not trim:
        return True
    v = _find_vehicle(make, model)
    if v is None:
        return True
    allowed = v.get("trims")
    if not allowed:
        return True
    t = _normalize_trim(trim)
    return any(
        t == a.lower().replace("-", " ") or t.startswith(a.lower().replace("-", " ") + " ")
        for a in allowed
    )


def trim_belongs_to_different_model(make: str, model: str, trim: str | None) -> bool:
    """Return True if the trim matches another vehicle's allowlist but NOT this one.
    Catches Cars.com data contamination (e.g. "SEL Convenience" on a Sportage).
    """
    if not trim:
        return False
    t = _normalize_trim(trim)
    make_norm  = _norm(make)
    model_norm = _norm(model)
    this_model_allows = False
    other_model_matches = False
    for v in _VEHICLES:
        allowed = v.get("trims")
        if not allowed:
            continue
        matches = any(
            t == a.lower().replace("-", " ") or
            t.startswith(a.lower() + " ") or
            t.startswith(a.lower().replace("-", " ") + " ")
            for a in allowed
        )
        if _norm(v["make"]) == make_norm and _norm(v["model"]) == model_norm:
            this_model_allows = matches
        elif matches:
            other_model_matches = True
    # Contaminated if it matches another model's trims but NOT this model's trims
    return other_model_matches and not this_model_allows


def get_model_min_year(make: str, model: str) -> int | None:
    """Return per-model minimum year override, or None if not set."""
    v = _find_vehicle(make, model)
    return v.get("min_year") if v else None


def get_model_max_year(make: str, model: str) -> int | None:
    """Return per-model maximum year override, or None if not set."""
    v = _find_vehicle(make, model)
    return v.get("max_year") if v else None


def get_trim_min_year(make: str, model: str, trim: str | None) -> int | None:
    """Return per-trim minimum year override, or None if not set."""
    if not trim:
        return None
    v = _find_vehicle(make, model)
    if v is None:
        return None
    t = _normalize_trim(trim)
    for allowed_trim, min_yr in v.get("trim_min_year", {}).items():
        a = _normalize_trim(allowed_trim)
        if t == a or t.startswith(a + " "):
            return min_yr
    return None


def get_trim_max_year(make: str, model: str, trim: str | None) -> int | None:
    """Return per-trim maximum year override, or None if not set."""
    if not trim:
        return None
    v = _find_vehicle(make, model)
    if v is None:
        return None
    t = _normalize_trim(trim)
    for allowed_trim, max_yr in v.get("trim_max_year", {}).items():
        a = _normalize_trim(allowed_trim)
        if t == a or t.startswith(a + " "):
            return max_yr
    return None


def get_cargurus_ids() -> list[tuple[str, str, str]]:
    """Return [(make, model, cargurus_id)] for vehicles that have a non-null cargurus_id."""
    return [(v["make"], v["model"], v["cargurus_id"])
            for v in _VEHICLES if v.get("cargurus_id")]


def canonical_trim(make: str, model: str, trim: str | None) -> str | None:
    """Return the canonical allowlist trim for this raw scraped trim, or None if no trim.

    Scrapers often append suffixes ("xDrive30i AWD", "xDrive30i Sport Activity Vehicle")
    or reorder tokens ("30 xDrive" instead of "xDrive30i"). This normalizes to the
    canonical name from cars.json. Falls back to raw trim if no match found.
    """
    if not trim:
        return None
    v = _find_vehicle(make, model)
    if not v or not v.get("trims"):
        return trim
    t = _normalize_trim(trim)
    # Pass 1 — prefix match: "xDrive30i AWD" → "xDrive30i"
    for allowed in v["trims"]:
        a = allowed.lower().replace("-", " ")
        if t == a or t.startswith(a + " "):
            return allowed
    # Pass 2 — BMW token-reorder: "30 xDrive" → "xDrive30i", "xDrive30" → "xDrive30i"
    # Extract drive type and number from input, then match canonical trim with same pair.
    import re as _re
    t_flat = t.replace(" ", "")
    for drive in ("xdrive", "sdrive"):
        if drive in t_flat:
            num = _re.sub(r"[^0-9]", "", t_flat.replace(drive, ""))  # e.g. "30" from "30xdrive"
            for allowed in v["trims"]:
                a_flat = allowed.lower().replace(" ", "").replace("-", "")
                if a_flat.startswith(drive) and _re.sub(r"[^0-9]", "", a_flat) == num:
                    return allowed

    # Pass 3 — whole-word substring match for formats like "B5 Plus Dark Theme AWD"
    # where the canonical trim name appears as a standalone word anywhere in the string.
    for allowed in v["trims"]:
        a = allowed.lower().replace("-", " ")
        if _re.search(r"\b" + _re.escape(a) + r"\b", t):
            return allowed

    return trim


