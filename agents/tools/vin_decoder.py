"""NHTSA vPIC VIN decoder — fills in trim/drivetrain from the VIN itself."""
import re
import requests

_NHTSA_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/decodevin/{}?format=json"
_NULL_VALUES = {"", "not applicable", "null", "0", "none"}
_VIN_RE = re.compile(r'^[A-HJ-NPR-Z0-9]{17}$', re.IGNORECASE)

# Module-level cache: vin → result dict. Returns the same dict object on hits;
# callers must treat it as read-only (only call .get() — never mutate).
_cache: dict[str, dict] = {}


def _clean(val: str | None) -> str | None:
    if not val or val.strip().lower() in _NULL_VALUES:
        return None
    return val.strip()


def decode_vin(vin: str) -> dict:
    """Decode a VIN via the free NHTSA vPIC API.

    Returns a dict with keys: make, model, year, trim, drive_type, body_class.
    Returns an empty dict on any network/parse error or invalid VIN format.
    Results are cached per-VIN. Treat the returned dict as read-only.
    """
    if not vin or not _VIN_RE.match(vin):
        return {}
    if vin in _cache:
        return _cache[vin]
    try:
        resp = requests.get(_NHTSA_URL.format(vin), timeout=6)
        if resp.status_code != 200:
            return {}
        raw = {r["Variable"]: r["Value"] for r in resp.json().get("Results", [])}
        result = {
            "make":       _clean(raw.get("Make")),
            "model":      _clean(raw.get("Model")),
            "year":       _clean(raw.get("Model Year")),
            "trim":       _clean(raw.get("Trim")),
            "drive_type": _clean(raw.get("Drive Type")),
            "body_class": _clean(raw.get("Body Class")),
        }
    except Exception:
        return {}
    _cache[vin] = result
    return result
