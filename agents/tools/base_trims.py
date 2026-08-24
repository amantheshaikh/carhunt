"""
Vehicle config loader. Single source of truth is agents/cars.json.
"""
import json
import os
import re
import sys
from typing import Optional

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


# Canonical (make, model) casing keyed by their lowercased equivalents.
# Scrapers return inconsistent casing (ALL CAPS, title case, etc.); this normalizes to cars.json.
_CANONICAL_NAMES: dict[tuple[str, str], tuple[str, str]] = {
    (v["make"].lower(), v["model"].lower()): (v["make"], v["model"])
    for v in _VEHICLES
}


def normalize_listing_model(make: str, model: str, trim: str | None = None) -> tuple[str, str]:
    """Return (canonical_make, canonical_model) for a scraped listing.

    Applies XC60 gas/PHEV split then looks up canonical casing from cars.json.
    Call this before any DB insert or allowlist check.
    """
    model = split_xc60_model(make, model, trim)
    canonical = _CANONICAL_NAMES.get((make.lower(), model.lower()))
    if canonical:
        make, model = canonical
    return make, model


def split_xc60_model(make: str, model: str, raw_trim: str) -> str:
    """Return canonical model name for Volvo XC60 listings, splitting gas vs PHEV by trim text.

    XC60 gas (B5) and XC60 Recharge T8 PHEV share the same CarGurus model page.
    We store them as separate DB model names. Any caller receiving an XC60 listing
    (from any scraper) should call this to get the correct model string before saving.
    """
    if make.lower() != "volvo" or _norm(model) not in ("xc60", "xc60recharge"):
        return model
    trim_lower = (raw_trim or "").lower()
    return "XC60 Recharge" if ("recharge" in trim_lower or "t8" in trim_lower) else "XC60"



_ENGINE_PREFIXES = re.compile(r"^(?:\d+\.\d+[tTlLiIdDhH]|\d{2,4}[hHe])\s+", re.IGNORECASE)


def _normalize_trim(t: str) -> str:
    """Normalize trim for comparison: lowercase, hyphens→spaces, strip engine prefix."""
    t = t.lower().strip().replace("-", " ")
    t = _ENGINE_PREFIXES.sub("", t)  # strip "2.0T ", "3.5L ", etc.
    return t


def _trim_matches_any(t: str, allowed: list[str]) -> bool:
    """Return True if normalized trim t matches any allowed trim via prefix or whole-word substring.

    Three checks mirror canonical_trim's Pass 1 and Pass 3:
      1. Exact match or prefix:   "plus dark theme awd" starts with "plus " → True
      2. Whole-word substring:    "b5 plus dark theme awd" contains word "plus" → True
         (handles Volvo "B5 Plus Dark Theme AWD" and similar scraped formats)
    """
    for a in allowed:
        a_norm = a.lower().replace("-", " ")
        if t == a_norm or t.startswith(a_norm + " "):
            return True
    # whole-word substring (same as canonical_trim Pass 3)
    for a in allowed:
        a_norm = a.lower().replace("-", " ")
        if re.search(r"\b" + re.escape(a_norm) + r"\b", t):
            return True
    return False


def _engine_filter_passes(v: dict, trim: str, source: str | None = None) -> bool:
    """If the vehicle has an engine_filter list, the raw trim must contain one of the
    allowed engine designations (e.g. '2.5T') before prefix-stripping. This lets us
    distinguish variants like GV70 2.5T vs 3.5T that share the same trim names.

    Exception: when source='carscom', Cars.com's model slug already scopes to the
    correct variant, so requiring the keyword in the trim string is redundant and is
    skipped when the model name itself contains the filter term."""
    engine_filter = v.get("engine_filter")
    if not engine_filter:
        return True
    model_name = v.get("model", "").lower()
    if any(ef.lower() in model_name for ef in engine_filter):
        return True
    raw = trim.lower()
    return any(ef.lower() in raw for ef in engine_filter)


# ---------------------------------------------------------------------------
# LLM trim normalization fallback
# ---------------------------------------------------------------------------

# In-process cache: (make_lower, model_lower, trim_lower) → canonical or None.
# Managed by canonical_trim; _llm_canonical_trim itself is a pure LLM call so
# it can be monkeypatched in tests without breaking cache behaviour.
_CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", ".trim_cache.json")
_llm_trim_cache: dict[tuple[str, str, str], Optional[str]] = None


def _load_trim_cache():
    global _llm_trim_cache
    if _llm_trim_cache is not None:
        return
    _llm_trim_cache = {}
    if "pytest" in sys.modules:
        return  # skip on-disk cache loading during unit testing
    if os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE) as f:
                data = json.load(f)
                for k, v in data.items():
                    parts = k.split("::")
                    if len(parts) == 3:
                        _llm_trim_cache[(parts[0], parts[1], parts[2])] = v
        except Exception:
            pass


def _save_trim_cache():
    if _llm_trim_cache is None or "pytest" in sys.modules:
        return
    try:
        data = {f"{k[0]}::{k[1]}::{k[2]}": v for k, v in _llm_trim_cache.items()}
        with open(_CACHE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


# Shared LLM client — instantiated once, reused across all trim lookups.
_llm_client = None

def get_llm_client():
    global _llm_client
    if _llm_client is None:
        try:
            from openai import OpenAI as _OpenAI
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if api_key:
                _llm_client = _OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
        except ImportError:
            pass
    return _llm_client


def _llm_canonical_trim(make: str, model: str, raw_trim: str, allowed_trims: list[str]) -> Optional[str]:
    """Pure LLM call: map raw_trim to one of allowed_trims.

    Returns the matched canonical trim (guaranteed to be in allowed_trims), or
    None if no reasonable match.  Callers are responsible for caching.
    """
    client = get_llm_client()
    if client is None:
        return None
    try:
        trims_str = ", ".join(f'"{t}"' for t in allowed_trims)
        resp = client.chat.completions.create(
            model="deepseek/deepseek-v4-flash",
            messages=[{
                "role": "user",
                "content": (
                    f'Map "{raw_trim}" ({make} {model}) to one of: {trims_str}. '
                    f"Match only if it is an alternate format/abbreviation of the SAME tier "
                    f'(e.g. "PREM PLUS"→"Premium Plus"). A shorter name does NOT match a '
                    f'longer different-tier trim (e.g. "SEL"≠"SEL Convenience"). '
                    f'Reply with the exact trim name or "null".'
                ),
            }],
            max_tokens=30,
            temperature=0,
        )
        answer = (resp.choices[0].message.content or "").strip().rstrip(".").strip('"').strip("'").strip()
        if answer.lower() == "null" or not answer:
            return None
        allowed_lower = {t.lower(): t for t in allowed_trims}
        return allowed_lower.get(answer.lower())
    except Exception:
        return None


def is_trim_allowed_for_training(make: str, model: str, trim: str | None, source: str | None = None) -> bool:
    """Like is_trim_allowed but uses training_trims when present.
    training_trims includes lower base trims (e.g. Blue, SEL, LX) that broaden the
    depreciation curve for ML training without appearing in sourcing results.
    Falls back to trims if training_trims is not set.

    For engine-filtered models (e.g. GV70 2.5T vs 3.5T), once the engine_filter
    check passes we accept the listing even if the sub-trim isn't recognized — the
    engine variant is the critical gate, and CarGurus sometimes only knows the engine
    designation (e.g. '2.5T AWD') without the full trim name.
    """
    if not trim:
        return True
    v = _find_vehicle(make, model)
    if v is None:
        return True
    if not _engine_filter_passes(v, trim, source=source):
        return False
    # If vehicle uses engine_filter, passing that check is sufficient for training.
    if v.get("engine_filter"):
        return True
    allowed = v.get("training_trims") or v.get("trims")
    if not allowed:
        return True
    return _trim_matches_any(_normalize_trim(trim), allowed)


def _match_bmw_token_reorder(norm_trim: str, allowed_trims: list[str]) -> str | None:
    t_flat = norm_trim.replace(" ", "")
    for drive in ("xdrive", "sdrive"):
        if drive in t_flat:
            num = re.sub(r"[^0-9]", "", t_flat.replace(drive, ""))
            if not num:
                continue
            for allowed in allowed_trims:
                a_flat = allowed.lower().replace(" ", "").replace("-", "")
                if a_flat.startswith(drive) and re.sub(r"[^0-9]", "", a_flat) == num:
                    return allowed
    return None

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
    if not _engine_filter_passes(v, trim):
        return False
    allowed = v.get("trims")
    if not allowed:
        return True
    
    t = _normalize_trim(trim)
    if _trim_matches_any(t, allowed):
        return True

    # Check for BMW token-reordering (e.g. "30 xDrive" -> xDrive30i)
    if _match_bmw_token_reorder(t, allowed):
        return True
    return False


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


def canonical_trim(make: str, model: str, trim: str | None) -> str | None:
    """Return the canonical allowlist trim for this raw scraped trim, or None if no trim.

    Scrapers often append suffixes ("xDrive30i AWD", "xDrive30i Sport Activity Vehicle")
    or reorder tokens ("30 xDrive" instead of "xDrive30i"). This normalizes to the
    canonical name from cars.json. Falls back to raw trim if no match found.
    """
    if not trim:
        return None
    v = _find_vehicle(make, model)
    if not v:
        return trim
    trims = v.get("training_trims") or v.get("trims")
    if not trims:
        return trim
    t = _normalize_trim(trim)
    # Pass 1 — prefix match: "xDrive30i AWD" → "xDrive30i"
    # Sort longest-first so "SE R-Line Black" wins over "SE" when both are prefix-compatible.
    for allowed in sorted(trims, key=len, reverse=True):
        a = allowed.lower().replace("-", " ")
        if t == a or t.startswith(a + " "):
            return allowed
    # Pass 2 — BMW token-reorder: "30 xDrive" → "xDrive30i", "xDrive30" → "xDrive30i"
    # Extract drive type and number from input, then match canonical trim with same pair.
    bmw_match = _match_bmw_token_reorder(t, trims)
    if bmw_match:
        return bmw_match

    # Pass 3 — whole-word substring match for formats like "B5 Plus Dark Theme AWD"
    # where the canonical trim name appears as a standalone word anywhere in the string.
    # Sort longest-first for the same reason as Pass 1.
    for allowed in sorted(trims, key=len, reverse=True):
        a = allowed.lower().replace("-", " ")
        if re.search(r"\b" + re.escape(a) + r"\b", t):
            return allowed

    # Pass 4 — LLM fallback for abbreviations and non-standard dealer naming
    # (e.g. "ADV PLUS" → "Advanced Plus", "PREM+" → "Premium Plus").
    # Cache lives here so monkeypatching _llm_canonical_trim in tests is effective.
    _load_trim_cache()
    cache_key = (make.lower(), model.lower(), trim.lower())
    if cache_key not in _llm_trim_cache:
        _llm_trim_cache[cache_key] = _llm_canonical_trim(make, model, trim, trims)
        _save_trim_cache()
    llm = _llm_trim_cache[cache_key]
    # Validate the return value is actually in the allowed list (defense in depth
    # against callers who monkeypatch _llm_canonical_trim for testing).
    if llm and llm in trims:
        print(f"  [trim:llm] {make} {model} '{trim}' → '{llm}'", flush=True)
        return llm

    return trim


