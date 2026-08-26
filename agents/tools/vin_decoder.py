"""NHTSA vPIC VIN decoder — fills in trim/drivetrain from the VIN itself."""
import functools
import requests

_NHTSA_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/decodevin/{}?format=json"
_NULL_VALUES = {"", "not applicable", "null", "0", "none"}


def _clean(val: str | None) -> str | None:
    if not val or val.strip().lower() in _NULL_VALUES:
        return None
    return val.strip()


@functools.lru_cache(maxsize=4096)
def decode_vin(vin: str) -> dict:
    """Decode a VIN via the free NHTSA vPIC API.

    Returns a dict with keys: make, model, year, trim, drive_type, body_class.
    Returns an empty dict on any network/parse error.
    Results are cached per-VIN (up to 4096 entries) to avoid duplicate calls
    across the listing loop.
    """
    try:
        resp = requests.get(_NHTSA_URL.format(vin), timeout=6)
        if resp.status_code != 200:
            return {}
        raw = {r["Variable"]: r["Value"] for r in resp.json().get("Results", [])}
        return {
            "make":       _clean(raw.get("Make")),
            "model":      _clean(raw.get("Model")),
            "year":       _clean(raw.get("Model Year")),
            "trim":       _clean(raw.get("Trim")),
            "drive_type": _clean(raw.get("Drive Type")),
            "body_class": _clean(raw.get("Body Class")),
        }
    except Exception:
        return {}
