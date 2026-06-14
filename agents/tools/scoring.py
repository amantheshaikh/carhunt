import math


def detect_drivetrain(trim: str | None, model: str | None, raw_text: str | None = None) -> str:
    text = " " + " ".join(filter(None, [trim, model, raw_text])).lower() + " "
    if any(k in text for k in ("awd", "4wd", "4motion", "4x4", "all-wheel", "all wheel",
                                "eawd",      # Volvo eAWD (XC60 Recharge)
                                "xdrive",    # BMW xDrive = AWD
                                "m35i",      # BMW X1 M35i is always xDrive AWD
                                "m40i",      # BMW X3 M40i is always xDrive AWD
                                "quattro",   # Audi quattro = AWD
                                "4matic",    # Mercedes-Benz 4MATIC = AWD
                                " b6 ",      # Volvo B6 = AWD standard
                                )):
        return "awd"
    if any(k in text for k in ("fwd", "front-wheel", "front wheel", "2wd", "rwd",
                                "sdrive",    # BMW sDrive = single-axle (RWD/FWD, not AWD)
                                " b4 ",      # Volvo B4 mild hybrid = FWD only
                                )):
        return "fwd"
    return "unknown"


def detect_powertrain(trim: str | None, model: str | None, raw_text: str | None = None) -> str:
    text = " " + " ".join(filter(None, [trim, model, raw_text])).lower() + " "
    if any(k in text for k in ("plug-in", "phev", "plug in hybrid", "recharge", " t8 ")):
        return "phev"
    if any(k in text for k in ("hybrid", "350h", "400h", "500h")):
        return "hybrid"
    if any(k in text for k in ("electric", " ev ", "bev", "ioniq")):
        return "electric"
    return "gas"


def score_listing(price: int, ml_price: int | None, residual_std: float = 0.0) -> float | None:
    """Score 0-100 based purely on price deviation from ML fair value.

    When residual_std > 0 (per-model OOF spread), uses a z-score sigmoid so
    that the same dollar discount is worth more for tightly-priced models and
    less for wide-spread models. k=0.94 calibrates so 1σ below fair ≈ 72
    (Great Deal) and 2σ ≈ 88 (Exceptional Deal).

    Falls back to percentage-based sigmoid (k=11) for the global model.
    Returns None when no ML prediction exists.
    """
    if not ml_price or ml_price <= 0:
        return None
    if residual_std > 0:
        effective_std = max(600.0, residual_std)
        z = (ml_price - price) / effective_std
        return round(100.0 / (1.0 + math.exp(-z * 0.94)), 1)
    spread = (ml_price - price) / ml_price
    return round(100.0 / (1.0 + math.exp(-spread * 11)), 1)
