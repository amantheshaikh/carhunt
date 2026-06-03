"""
Per-model ML fair-price estimator.

Trains one GradientBoostingRegressor per target vehicle (Tucson Hybrid,
Sportage Hybrid, Tiguan, Envision). Each model learns that vehicle's specific
depreciation curve, trim price ladder, and AWD premium independently —
no cross-model contamination.

Features (no model identity needed — all training data is the same vehicle):
  - year
  - mileage  (log1p-scaled — compresses low-mileage premium to avoid over-rewarding near-new cars)
  - trim_level  (ordinal 1-6 within that model)
  - drivetrain  (0=FWD, 0.5=unknown, 1=AWD)
  - powertrain  (0=gas, 0.8=hybrid, 1.0=PHEV/electric)

Saved as: agents/tools/price_model_{model_slug}.pkl
Falls back to global model if per-model has insufficient data (<30 rows).

Split models: vehicles in _POWERTRAIN_SPLIT (e.g. XC60) also train separate
price_model_{slug}_gas.pkl and price_model_{slug}_phev.pkl so gas B5 and
Recharge T8 PHEV depreciation curves don't bleed into each other.
predict_fair_price() selects the right sub-model based on detected powertrain.
"""
import os
from datetime import datetime

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from tools.db import fetch_all
from tools.scoring import detect_drivetrain, detect_powertrain
from tools.base_trims import _VEHICLES as _VEH_LIST

# Target year range per model (from cars.json) — used to bound training data
_MODEL_YEAR_RANGE: dict[str, tuple[int, int | None]] = {
    v["model"].lower(): (v.get("min_year", 0), v.get("max_year"))
    for v in _VEH_LIST
}

_CURRENT_YEAR = datetime.now().year

_DIR = os.path.dirname(__file__)

_MIN_ROWS = 30   # minimum training rows to fit a per-model estimator

_TRIM_LEVELS: dict[str, dict[str, int]] = {
    "tucson":           {"se": 1, "sel": 2, "sel convenience": 3, "xrt": 3, "n line": 4, "sel premium": 4, "limited": 5},
    "tucson hybrid":    {"blue": 1, "sel": 2, "sel convenience": 3, "n line": 4, "limited": 5, "sel n-line": 4},
    "sportage":         {"lx": 1, "ex": 2, "sx": 3, "sx prestige": 4, "x-line": 4, "x-pro": 5, "x-pro prestige": 6, "prestige": 4},
    "sportage hybrid":  {"lx": 1, "ex": 2, "sx": 3, "sx prestige": 4, "x-line": 4, "x-pro": 5, "x-pro prestige": 6, "prestige": 4},
    "tiguan":           {"s": 1, "se": 2, "se r-line": 3, "se r-line black": 3, "sel": 4, "sel premium": 5, "sel r-line black": 5},
    "envision":         {"preferred": 1, "sport touring": 2, "avenir": 3},
    "x1":               {"sdrive28i": 1, "xdrive28i": 2},
    "x3":               {"sdrive30i": 1, "xdrive30i": 2},
    "nx 350h":          {"premium": 2, "luxury": 3, "f sport": 3},
    "xc40":             {"core": 1, "plus": 2, "ultimate": 3},
    "xc60":             {"core": 1, "plus": 2, "ultimate": 3},
}

_ALL_MODELS = list(_TRIM_LEVELS.keys())

# Models where gas and PHEV have sufficiently different depreciation curves to
# warrant separate pkl files.  train() produces {slug}_gas.pkl + {slug}_phev.pkl
# in addition to the combined {slug}.pkl (kept as fallback).
# predict_fair_price() detects powertrain and selects the right sub-model.
_POWERTRAIN_SPLIT: set[str] = {"xc60"}


def _split_pkl_path(model: str, powertrain: str) -> str:
    """e.g. price_model_xc60_gas.pkl"""
    pt_slug = "phev" if powertrain in ("phev", "electric", "hybrid") else "gas"
    return os.path.join(_DIR, f"price_model_{_model_slug(model)}_{pt_slug}.pkl")


def _model_slug(model: str) -> str:
    return model.lower().strip().replace(" ", "_").replace("-", "_")


def _pkl_path(model: str) -> str:
    return os.path.join(_DIR, f"price_model_{_model_slug(model)}.pkl")


def _trim_level(model: str, trim: str | None) -> float:
    levels = _TRIM_LEVELS.get(model.lower(), {})
    if not trim:
        return 2.0
    t = trim.lower().strip()
    best, best_len = None, 0
    for key, val in levels.items():
        if t.startswith(key) and len(key) > best_len:
            best, best_len = val, len(key)
    return float(best) if best is not None else 2.5


def _mileage_bucket(miles: float) -> float:
    """Convert odometer to a 4-level ordinal (0=low, 3=very high).
    Prevents the model from over-weighting small low-mileage differences:
    a 1k-mile and 12k-mile car are both 'low mileage' and score similarly.
      0 = under 15k   (low)
      1 = 15k–30k     (moderate)
      2 = 30k–60k     (high)
      3 = over 60k    (very high)
    """
    if miles < 15_000: return 0.0
    if miles < 30_000: return 1.0
    if miles < 60_000: return 2.0
    return 3.0


# Trim-level drivetrain overrides — when the trim name alone determines drivetrain.
# Checked before keyword detection; keyed by (make.lower(), model.lower(), trim.lower()).
_TRIM_DRIVETRAIN: dict[tuple[str, str, str], str] = {
    ("volkswagen", "tiguan", "sel r-line"): "awd",  # SEL R-Line is 4MOTION AWD only
}

# Model-year combinations where drivetrain is known regardless of trim text.
# Use make.lower() + model.lower() + min_year as key.
_DRIVETRAIN_DEFAULTS: dict[tuple[str, str, int], str] = {
    ("buick",   "envision",        2025): "awd",  # 2025+ Envision is AWD-only
    ("hyundai", "tucson hybrid",   2025): "awd",  # 2025+ Tucson Hybrid is AWD-only
    ("kia",     "sportage hybrid", 2026): "awd",  # 2026 Sportage Hybrid EX/SX Prestige are AWD-only
    ("lexus",   "nx 350h",         2023): "awd",  # NX 350h is AWD-only in US
    ("bmw",     "x1",              2023): "awd",  # 2023–2025 X1 only has xDrive28i (M35i excluded)
    ("bmw",     "x3",              2025): "awd",  # 2025 X3 only has xDrive30i
    ("volvo",   "xc60",            2023): "awd",  # XC60 B5/B6/T8 all AWD
    # XC40: B4 can be FWD, B5 is AWD — detect from trim text, no default override
}


def _drivetrain_for(row: dict, trim: str | None, model: str, raw: str | None) -> str:
    """Return drivetrain for this row, applying overrides before text detection."""
    make = (row.get("make") or "").lower()
    year = int(row.get("year") or 0)
    # 1. Trim-level override (most specific)
    if trim:
        td = _TRIM_DRIVETRAIN.get((make, model, trim.lower()))
        if td:
            return td
    # 2. Model-year override
    for (mk, mo, min_yr), dt in _DRIVETRAIN_DEFAULTS.items():
        if make == mk and model == mo and year >= min_yr:
            return dt
    # 3. Keyword detection from trim/model/raw text
    return row.get("drivetrain") or detect_drivetrain(trim, model, raw)


def _to_features(row: dict) -> list[float]:
    model = (row.get("model") or "").lower()
    trim  = row.get("trim")
    raw   = (row.get("raw") or {}).get("text") if isinstance(row.get("raw"), dict) else None
    raw_mileage = float(15_000 if row.get("mileage") is None else row.get("mileage"))
    return [
        float(row.get("year") or 2025),
        _mileage_bucket(raw_mileage),  # coarse buckets: 0-15k / 15k-30k / 30k-60k / 60k+
        _trim_level(model, trim),
        {"awd": 1.0, "fwd": 0.0, "unknown": 0.5}[_drivetrain_for(row, trim, model, raw)],
        {"electric": 1.0, "phev": 1.0, "hybrid": 0.8, "gas": 0.0}[
            row.get("powertrain") or detect_powertrain(trim, model, raw)],
    ]


# Feature order: [year, mileage, trim_level, drivetrain, powertrain]
# Monotone constraints enforce economic common sense:
#   year (+1): newer = more expensive
#   mileage (-1): more miles = less expensive
#   trim_level (+1): higher trim = more expensive
#   drivetrain (+1): AWD > FWD
#   powertrain (+1): hybrid/EV > gas
_MONOTONIC_CST = [1, -1, 1, 1, 1]  # year+, mileage-, trim_level+, drivetrain+, powertrain+


def _make_pipeline() -> Pipeline:
    # HistGradientBoostingRegressor is faster than GBR and supports monotone_cst.
    # StandardScaler is a no-op for tree models but kept for interface consistency.
    return Pipeline([
        ("scaler", StandardScaler()),
        ("gbr", HistGradientBoostingRegressor(
            max_iter=200, max_depth=4, learning_rate=0.05,
            min_samples_leaf=3, random_state=42,
            monotonic_cst=_MONOTONIC_CST,
        )),
    ])


# Models where the listings table has known source contamination (e.g. CarGurus hybrid
# pages serving non-hybrid listings). For these, training uses only price_training_data,
# which is collected via validated model-specific queries in training_collector.py.
_LISTINGS_EXCLUDED_MODELS = {"tucson hybrid"}


# Models where older inventory is a different vehicle platform — exclude pre-platform rows
# so the model isn't contaminated by a fundamentally different car's depreciation curve.
_PLATFORM_MIN_YEAR: dict[str, int] = {
    "tucson hybrid":   2022,  # TNGA-K hybrid platform gen launched 2022
    "sportage hybrid": 2023,  # NQ5 platform launched 2023
    "tiguan":          2022,  # MK2 LCI refresh 2022 (new interior/features)
    "envision":        2021,  # 2nd gen launched 2021; 2020 is first gen
    "x1":              2023,  # U11 gen launched 2023; F48 (2016-2022) is a different car
    "x3":              2022,  # G01 LCI refresh 2022
    "nx 350h":         2022,  # TNGA-K gen launched 2022; prior NX hybrids are different generation
    "xc40":            2023,  # CMA platform refresh; 2022 and earlier is a different gen
    "xc60":            2023,  # SPA platform refresh for 2023+
}


def load_training_data_for(model_name: str, powertrain_filter: str | None = None) -> tuple[list, list]:
    key = model_name.lower()
    platform_min          = _PLATFORM_MIN_YEAR.get(key, 0)
    target_min, target_max = _MODEL_YEAR_RANGE.get(key, (0, None))
    effective_min         = max(platform_min, target_min)
    # PHEVs can exceed the normal $55k cap (XC60 T8 goes to ~$60k)
    max_price = 65_000 if powertrain_filter == "phev" else 55_000

    # price_training_data: apply per-year P10 floor to cut accident/salvage/lemon tail.
    # Listings table is already accident-filtered by _is_valid() — no P10 needed there.
    cte_max_yr   = "AND cte.year <= %s" if target_max else ""
    join_max_yr  = "AND ptd.year <= %s" if target_max else ""
    max_year_params = [target_max] if target_max else []
    cte_pt_sql   = "AND cte.powertrain = %s" if powertrain_filter else ""
    join_pt_sql  = "AND ptd.powertrain = %s" if powertrain_filter else ""
    pt_params    = [powertrain_filter] if powertrain_filter else []

    train_rows = fetch_all(f"""
        WITH price_floors AS (
            SELECT cte.year,
                   PERCENTILE_CONT(0.10) WITHIN GROUP (ORDER BY cte.price)::int AS p10
            FROM price_training_data cte
            WHERE lower(cte.model) = %s
              AND cte.price BETWEEN 20000 AND {max_price} AND cte.mileage <= 80000
              AND cte.year >= %s {cte_max_yr}
              AND (cte.year IS NULL OR cte.mileage::float / (%s - cte.year + 1) <= 14000)
              {cte_pt_sql}
            GROUP BY cte.year
            HAVING count(*) >= 5
        )
        SELECT ptd.make, ptd.model, ptd.year, ptd.mileage, ptd.price, ptd.trim, ptd.powertrain
        FROM price_training_data ptd
        JOIN price_floors pf ON ptd.year = pf.year
        WHERE lower(ptd.model) = %s
          AND ptd.price IS NOT NULL AND ptd.mileage IS NOT NULL
          AND ptd.price BETWEEN 20000 AND {max_price} AND ptd.mileage <= 80000
          AND ptd.year >= %s {join_max_yr}
          AND ptd.mileage::float / (%s - ptd.year + 1) <= 14000
          AND ptd.price >= pf.p10
          {join_pt_sql}
    """, (key, effective_min, *max_year_params, _CURRENT_YEAR, *pt_params,
          key, effective_min, *max_year_params, _CURRENT_YEAR, *pt_params))

    if key in _LISTINGS_EXCLUDED_MODELS:
        rows = list(train_rows)
    else:
        max_yr_sql = f"AND year <= {target_max}" if target_max else ""
        listing_rows = fetch_all(f"""
            SELECT make, model, year, mileage, price, trim FROM listings
            WHERE price IS NOT NULL AND mileage IS NOT NULL
              AND condition = 'used' AND price BETWEEN 20000 AND {max_price}
              AND mileage <= 80000 AND lower(model) = %s
              AND year >= %s {max_yr_sql}
        """, (key, effective_min))
        rows = list(train_rows) + list(listing_rows)
        # For split models, filter listings by detected powertrain
        if powertrain_filter:
            pt_slug = "phev" if powertrain_filter in ("phev", "electric", "hybrid") else "gas"
            rows = [
                r for r in rows
                if (r.get("powertrain") or detect_powertrain(r.get("trim"), r.get("model"))) == pt_slug
                or (r.get("powertrain") or detect_powertrain(r.get("trim"), r.get("model"))) == powertrain_filter
            ]

    X, y = [], []
    for row in rows:
        try:
            X.append(_to_features(row))
            y.append(float(row["price"]))
        except Exception as e:
            print(f"  [price_model] skipping row for {model_name}: {e}")
            continue
    return X, y


def _fit_and_report(model_name: str, X: list, y: list, label: str, pkl_path: str, save: bool) -> Pipeline | None:
    if len(X) < _MIN_ROWS:
        print(f"  [{label}] only {len(X)} rows — skipping (needs {_MIN_ROWS}+)")
        return None
    pipe = _make_pipeline()
    pipe.fit(X, y)
    preds  = pipe.predict(X)
    errors = np.abs(np.array(preds) - np.array(y))
    print(f"  [{label}] n={len(X)}  MAE=${np.mean(errors):,.0f}  max=${np.max(errors):,.0f}")
    if save:
        joblib.dump(pipe, pkl_path)
    return pipe


def train(save: bool = True) -> dict[str, Pipeline]:
    """Train one model per vehicle. Returns {model_name: pipeline}."""
    fitted: dict[str, Pipeline] = {}
    all_X, all_y = [], []

    for model_name in _ALL_MODELS:
        X, y = load_training_data_for(model_name)
        all_X.extend(X)
        all_y.extend(y)

        pipe = _fit_and_report(model_name, X, y, model_name, _pkl_path(model_name), save)
        if pipe:
            fitted[model_name] = pipe

        # For split models, also train powertrain-specific sub-models
        if model_name in _POWERTRAIN_SPLIT:
            for pt in ("gas", "phev"):
                X_pt, y_pt = load_training_data_for(model_name, powertrain_filter=pt)
                label = f"{model_name}/{pt}"
                sub_pipe = _fit_and_report(model_name, X_pt, y_pt, label, _split_pkl_path(model_name, pt), save)
                if sub_pipe:
                    fitted[f"{model_name}_{pt}"] = sub_pipe

    # Global fallback trained on all data combined
    if len(all_X) >= _MIN_ROWS:
        global_pipe = _make_pipeline()
        global_pipe.fit(all_X, all_y)
        fitted["_global"] = global_pipe
        if save:
            joblib.dump(global_pipe, _pkl_path("_global"))
        preds  = global_pipe.predict(all_X)
        errors = np.abs(np.array(preds) - np.array(all_y))
        print(f"  [_global]  n={len(all_X)}  MAE=${np.mean(errors):,.0f}")

    return fitted


# In-process model cache — avoids re-reading .pkl files for every listing in a run.
# Invalidated by process restart; retrain writes new .pkl files and the next
# orchestrator invocation starts a fresh process.
_model_cache: dict[str, object] = {}


def _load_model(pkl_path: str) -> object | None:
    """Load a model from disk, caching the result for the process lifetime."""
    if pkl_path in _model_cache:
        return _model_cache[pkl_path]
    if not os.path.exists(pkl_path):
        return None
    try:
        model = joblib.load(pkl_path)
        _model_cache[pkl_path] = model
        return model
    except Exception:
        return None


def _detect_powertrain_for(listing: dict) -> str:
    trim = listing.get("trim")
    model = listing.get("model", "")
    raw = (listing.get("raw") or {}).get("text") if isinstance(listing.get("raw"), dict) else None
    return listing.get("powertrain") or detect_powertrain(trim, model, raw)


def predict_fair_price(listing: dict) -> int | None:
    """Predict fair price using the per-model estimator, falling back to global."""
    model_name = (listing.get("model") or "").lower().strip()
    features   = [_to_features(listing)]

    # For split models, prefer the powertrain-specific sub-model
    if model_name in _POWERTRAIN_SPLIT:
        pt = _detect_powertrain_for(listing)
        split_model = _load_model(_split_pkl_path(model_name, pt))
        if split_model is not None:
            try:
                return int(round(split_model.predict(features)[0]))
            except Exception:
                pass

    model = _load_model(_pkl_path(model_name))
    if model is not None:
        try:
            return int(round(model.predict(features)[0]))
        except Exception:
            pass

    global_model = _load_model(_pkl_path("_global"))
    if global_model is not None:
        try:
            return int(round(global_model.predict(features)[0]))
        except Exception:
            pass

    return None


_DRIVETRAIN_LABEL = {1.0: "AWD", 0.0: "FWD", 0.5: "Unknown drivetrain"}
_POWERTRAIN_LABEL = {1.0: "Electric/PHEV", 0.8: "Hybrid", 0.0: "Gas"}


def explain_listing(listing: dict) -> dict | None:
    """Return SHAP-based price breakdown for a listing.

    Returns:
        {
          "base_value": 32000,          # average price for this model in training data
          "predicted": 34500,           # ML fair price (= base + sum of contributions)
          "features": [
            {"label": "Year 2025",      "delta": 800,  "raw": 2025},
            {"label": "8,000 miles",    "delta": 600,  "raw": 8000},
            {"label": "Trim XLE",       "delta": -200, "raw": 2},
            {"label": "AWD",            "delta": 900,  "raw": 1.0},
            {"label": "Hybrid",         "delta": 400,  "raw": 0.8},
          ]
        }
    Only includes features with |delta| >= $50 to avoid noise.
    Returns None if no model is available or shap is not installed.
    """
    try:
        import shap as _shap
        import numpy as _np
    except ImportError:
        return None

    model_name = (listing.get("model") or "").lower().strip()
    raw_features = _to_features(listing)
    X_raw = _np.array([raw_features])

    pipe = None
    if model_name in _POWERTRAIN_SPLIT:
        pt = _detect_powertrain_for(listing)
        pipe = _load_model(_split_pkl_path(model_name, pt))
    if pipe is None:
        pipe = _load_model(_pkl_path(model_name))
    if pipe is None:
        pipe = _load_model(_pkl_path("_global"))
    if pipe is None:
        return None

    try:
        scaler = pipe.named_steps["scaler"]
        gbr    = pipe.named_steps["gbr"]
        X_scaled = scaler.transform(X_raw)

        explainer  = _shap.TreeExplainer(gbr)
        shap_vals  = explainer.shap_values(X_scaled)[0]
        ev         = explainer.expected_value
        base_value = float(_np.asarray(ev).flatten()[0])
        predicted  = int(round(base_value + shap_vals.sum()))

        year           = int(raw_features[0])
        # raw_features[1] is the mileage bucket (0-3) — show actual miles in label
        mileage_actual = int(15_000 if listing.get("mileage") is None else listing.get("mileage"))
        trim_lvl   = raw_features[2]
        drivetrain = raw_features[3]
        powertrain = raw_features[4]

        trim_name = listing.get("trim") or f"Level {trim_lvl:.0f}"

        raw_labels = [
            (f"Year {year}",                  raw_features[0]),
            (f"{mileage_actual:,} miles",     mileage_actual),   # show actual miles, not log value
            (f"Trim — {trim_name[:25]}",      trim_lvl),
            (_DRIVETRAIN_LABEL.get(drivetrain, "Drivetrain"), drivetrain),
            (_POWERTRAIN_LABEL.get(powertrain, "Powertrain"), powertrain),
        ]

        features = [
            {"label": label, "delta": int(round(float(sv))), "raw": float(raw)}
            for (label, raw), sv in zip(raw_labels, shap_vals)
            if abs(sv) >= 50  # skip noise
        ]

        return {
            "base_value": int(round(base_value)),
            "predicted":  predicted,
            "features":   features,
        }
    except Exception:
        return None


if __name__ == "__main__":
    print("Training per-model price estimators...")
    train()
    print("Done.")
