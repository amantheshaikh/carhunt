import math


def detect_drivetrain(trim: str | None, model: str | None, raw_text: str | None = None) -> str:
    text = " ".join(filter(None, [trim, model, raw_text])).lower()
    if any(k in text for k in ("awd", "4wd", "4motion", "4x4", "all-wheel", "all wheel",
                                "eawd",      # Volvo eAWD (XC60 Recharge)
                                "xdrive",    # BMW xDrive = AWD
                                "m35i",      # BMW X1 M35i is always xDrive AWD
                                "m40i",      # BMW X3 M40i is always xDrive AWD
                                "quattro",   # Audi quattro = AWD
                                "4matic",    # Mercedes-Benz 4MATIC = AWD
                                )):
        return "awd"
    if any(k in text for k in ("fwd", "front-wheel", "front wheel", "2wd", "rwd",
                                "sdrive",    # BMW sDrive = single-axle (RWD/FWD, not AWD)
                                )):
        return "fwd"
    return "unknown"


def detect_powertrain(trim: str | None, model: str | None, raw_text: str | None = None) -> str:
    text = " ".join(filter(None, [trim, model, raw_text])).lower()
    if any(k in text for k in ("plug-in", "phev", "plug in hybrid", "recharge")):
        return "phev"
    if any(k in text for k in ("hybrid",)):
        return "hybrid"
    if any(k in text for k in ("electric", " ev ", "bev", "ioniq")):
        return "electric"
    return "gas"


def score_listing(price: int, ml_price: int | None) -> float | None:
    """Score 0-100 based purely on price deviation from ML fair value.

    The ML model already encodes year, mileage, trim, drivetrain, and
    powertrain — so this single number captures the full deal quality.

    50 = at fair value
    75 ≈ 10% below (good deal)
    92 ≈ 20% below (great deal)
    25 ≈ 10% above (overpriced)
    Returns None when no ML prediction exists.
    """
    if not ml_price or ml_price <= 0:
        return None
    spread = (ml_price - price) / ml_price
    return round(100.0 / (1.0 + math.exp(-spread * 25)), 1)
