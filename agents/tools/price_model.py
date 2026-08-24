"""
Per-model stacked fair-price estimator.

Layer 1 — three base estimators, each capturing a different price signal:
  GB    HistGradientBoostingRegressor with monotone constraints — non-linear interactions
  KNN   distance-weighted k-nearest neighbours — local price cluster interpolation
  Ridge additive Ridge(α=200) — smooth global surface, regularised anchor

Layer 2 — meta-blend
  5-fold OOF predictions from all three base models are fed into a
  Ridge(positive=True, fit_intercept=False) that learns convex blend weights
  per vehicle slug.  Weights are normalised to sum=1 for interpretability.

SHAP waterfall still runs on the GB component (TreeExplainer requirement).
Models with < 30 rows are skipped; those listings return unscored.

Saved as: agents/tools/price_model_{model_slug}.pkl  (StackedModel dataclass)
Gas/PHEV splits (e.g. XC60 vs XC60 Recharge) are handled as separate cars.json
entries with separate model names, not via a code-level split.
"""
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env.local"))

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold
from sklearn.neighbors import KNeighborsRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from tools.db import fetch_all
from tools.scoring import detect_drivetrain, detect_powertrain
from tools.base_trims import _VEHICLES as _VEH_LIST, canonical_trim, _norm


@dataclass
class StackedModel:
    """Drop-in sklearn-compatible wrapper holding the three base models + blend weights."""
    gb:           Pipeline
    knn:          Pipeline
    ridge:        Pipeline
    meta_weights: np.ndarray  # shape (3,) — GB, KNN, Ridge; normalised to sum=1
    residual_std: float = 0.0  # OOF residual std dev — used to normalise deal score

    def predict(self, X) -> np.ndarray:
        X = np.array(X)
        base = np.column_stack([
            self.gb.predict(X),
            self.knn.predict(X[:, _KNN_FEATURE_COLS]),
            self.ridge.predict(X),
        ])
        return base @ self.meta_weights

# Override module name so that when trained via `python3 price_model.py` (which runs as __main__),
# joblib doesn't pickle it as __main__.StackedModel, but rather tools.price_model.StackedModel.
# This prevents AttributeError when loading the model from other entry points like orchestrator.py.
StackedModel.__module__ = "tools.price_model"

import sys
# Register this module in sys.modules under "tools.price_model" if executed as __main__
if __name__ == "__main__":
    sys.modules["tools.price_model"] = sys.modules["__main__"]
sys.modules["__main__"].StackedModel = StackedModel


# Target year range per model (from cars.json) — used to bound training data
_MODEL_YEAR_RANGE: dict[str, tuple[int, int | None]] = {
    v["model"].lower(): (v.get("min_year", 0), v.get("max_year"))
    for v in _VEH_LIST
}

_CURRENT_YEAR = datetime.now().year

_DIR = os.path.dirname(__file__)

_MIN_ROWS = 30   # minimum training rows to fit a per-model estimator

_TRIM_LEVELS: dict[str, dict[str, int]] = {
    "tucson hybrid":    {"blue": 1, "sel": 2, "sel convenience": 3, "n line": 4, "limited": 5, "sel n-line": 4},
    "sportage hybrid":  {"lx": 1, "ex": 2, "sx": 3, "sx prestige": 4, "x-line": 4, "x-pro": 5, "x-pro prestige": 6, "prestige": 4},
    "sportage phev":    {"ex": 1, "ex premium": 2, "x-line": 3, "x-line prestige": 4, "sx": 5},
    "gv70":             {"standard": 1, "select": 2, "advanced": 3, "advanced plus": 3.5, "sport prestige": 4},
    "tiguan":           {"s": 1, "se": 2, "se r-line": 3, "se r-line black": 4, "sel": 4, "sel premium": 5, "sel r-line black": 5},
    "encore gx":        {"preferred": 1, "sport touring": 2, "avenir": 3},
    "envision":         {"preferred": 1, "sport touring": 2, "avenir": 3},
    "x1":               {"sdrive28i": 1, "xdrive28i": 2},
    "x3":               {"sdrive30i": 1, "xdrive30i": 2},
    "xt4":              {"luxury": 1, "premium luxury": 2, "sport": 3},
    "nx 350h":          {"premium": 2, "luxury": 3, "f sport": 3},
    "xc40":             {"core": 1, "plus": 2, "ultimate": 3},
    "xc60":             {"core": 1, "plus": 2, "ultimate": 3},
    "xc60 recharge":   {"core": 1, "plus": 2, "ultimate": 3},
    "q5":               {"premium": 1, "premium plus": 2, "prestige": 3},
}

_ALL_MODELS = list(_TRIM_LEVELS.keys())


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


def _fallback_trim_premium(model: str, trim: str | None) -> float:
    current_ord = _trim_level(model, trim)
    return float((current_ord - 1.0) * 2000.0)


def _trim_premium(row: dict) -> float:
    make = (row.get("make") or "").lower().strip()
    model = (row.get("model") or "").lower().strip()
    year = int(row.get("year") or _CURRENT_YEAR)
    trim = row.get("trim")
    raw = (row.get("raw") or {}).get("text") if isinstance(row.get("raw"), dict) else None
    
    # 1. Get canonical trim
    canon_trim = canonical_trim(make, model, trim)
    
    # 2. Find vehicle config
    v = None
    for veg in _VEH_LIST:
        if veg["model"].lower() == model:
            v = veg
            break
            
    if not v or "msrp" not in v:
        return _fallback_trim_premium(model, trim)
        
    msrp_table = v["msrp"]
    if not msrp_table:
        return _fallback_trim_premium(model, trim)
        
    # Get closest year's MSRP list
    year_str = str(year)
    if year_str not in msrp_table:
        years = sorted([int(y) for y in msrp_table.keys()])
        if not years:
            return _fallback_trim_premium(model, trim)
        closest_yr = min(years, key=lambda y: abs(y - year))
        year_str = str(closest_yr)
        
    msrp_year_table = msrp_table[year_str]
    if not msrp_year_table:
        return _fallback_trim_premium(model, trim)
        
    base_msrp = min(msrp_year_table.values())
    
    # Find matching key
    def normalize_for_msrp(t: str) -> str:
        t = t.lower().strip().replace("-", "").replace(" ", "")
        t = re.sub(r'^\d+(\.\d+)?[tTlLiIdDhH]', '', t)
        t = re.sub(r'(awd|fwd)$', '', t)
        return t
        
    drivetrain = _drivetrain_for(row, trim, model, raw)
    dt_is_awd = (drivetrain == "awd")
    
    best_key = None
    best_score = -1
    canon_norm = normalize_for_msrp(canon_trim) if canon_trim else ""
    
    for key in msrp_year_table.keys():
        key_norm = normalize_for_msrp(key)
        if canon_norm and (canon_norm == key_norm or canon_norm in key_norm or key_norm in canon_norm):
            score = 10
            key_lower = key.lower()
            key_has_awd = any(k in key_lower for k in ("awd", "4motion", "4x4", "xdrive"))
            key_has_fwd = any(k in key_lower for k in ("fwd", "sdrive", "2wd"))
            
            # Prefer neutral keys (no drivetrain qualifier) so trim_premium
            # encodes only trim identity. The separate drivetrain feature
            # carries the FWD/AWD price delta — encoding it in trim_premium
            # too would double-count the AWD premium (e.g. Tiguan SE vs SE AWD).
            if not key_has_awd and not key_has_fwd:
                score += 5  # neutral key — preferred
            elif dt_is_awd and key_has_awd:
                score += 2  # drivetrain-specific key — fallback only
            elif not dt_is_awd and key_has_fwd:
                score += 2  # drivetrain-specific key — fallback only
                
            common_len = min(len(canon_norm), len(key_norm))
            score += common_len
            
            if score > best_score:
                best_score = score
                best_key = key
                
    if best_key:
        return float(msrp_year_table[best_key] - base_msrp)
        
    return _fallback_trim_premium(model, trim)


def _is_cpo(row: dict) -> bool:
    cpo_val = row.get("cpo")
    if cpo_val is not None:
        if isinstance(cpo_val, bool):
            return cpo_val
        if str(cpo_val).lower() in ("true", "1", "yes", "cpo"):
            return True
            
    insights = row.get("insights")
    if insights:
        if isinstance(insights, dict):
            if insights.get("cpo"):
                return True
        elif isinstance(insights, str):
            try:
                import json
                ins_dict = json.loads(insights)
                if ins_dict.get("cpo"):
                    return True
            except Exception:
                pass
                
    cond = (row.get("condition") or "").lower().strip()
    if cond == "cpo":
        return True
        
    trim = (row.get("trim") or "").lower()
    model = (row.get("model") or "").lower()
    raw = row.get("raw") or {}
    raw_text = ""
    if isinstance(raw, dict):
        raw_text = (raw.get("text") or "").lower()
    elif isinstance(raw, str):
        raw_text = raw.lower()
        
    text = f" {trim} {model} {raw_text} "
    return any(k in text for k in (" cpo ", " certified ", "certified pre-owned", "certified preowned"))


# Trim-level drivetrain overrides — when the trim name alone determines drivetrain.
# Checked before keyword detection; keyed by (make.lower(), model.lower(), trim.lower()).
_TRIM_DRIVETRAIN: dict[tuple[str, str, str], str] = {
    ("volkswagen", "tiguan",          "sel r-line"): "awd",  # SEL R-Line is 4MOTION AWD only
    ("kia",        "sportage hybrid", "x-line"):     "awd",  # X-Line is AWD-only across all years
    ("kia",        "sportage hybrid", "sx prestige"):"awd",  # SX Prestige is AWD-only across all years
    ("kia",        "sportage hybrid", "lx"):         "fwd",  # LX is FWD-only across all years
}

# Model-year combinations where drivetrain is known regardless of trim text.
# Use make.lower() + model.lower() + min_year as key.
_DRIVETRAIN_DEFAULTS: dict[tuple[str, str, int], str] = {
    ("buick",   "envision",        2024): "awd",  # 2024+ Envision is AWD-only
    ("hyundai", "tucson hybrid",   2025): "awd",  # 2025+ Tucson Hybrid is AWD-only
    ("kia",     "sportage hybrid", 2026): "awd",  # 2026 Sportage Hybrid EX/SX Prestige are AWD-only
    ("kia",     "sportage phev",   2023): "awd",  # Sportage PHEV is AWD-only (all years)
    ("genesis", "gv70",            2022): "awd",  # GV70 is AWD-only in US (all trims)
    ("lexus",   "nx 350h",         2022): "awd",  # NX 350h is AWD-only in US (from 2022 launch)
    ("bmw",     "x1",              2023): "awd",  # 2023–2025 X1 only has xDrive28i (M35i excluded)
    ("bmw",     "x3",              2025): "awd",  # 2025 X3 only has xDrive30i
    ("volvo",   "xc60",            2024): "awd",  # 2024+ XC60 all AWD; 2023 B5 can be FWD — detect from trim
    ("volvo",   "xc60 recharge",  2023): "awd",  # XC60 Recharge T8 eAWD
    ("volvo",   "xc40",           2024): "awd",  # 2024+ XC40: B4 FWD dropped, all variants AWD
    # XC40 2023: B4=FWD, B5 FWD or AWD — detect from raw trim text
    ("audi",    "q5",             2022): "awd",  # Q5 is always quattro AWD in US market
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
    # 3. Keyword detection from trim/model/raw text + listing URL slug
    url_slug = (row.get("listing_url") or "").replace("-", " ").replace("/", " ")
    combined_raw = " ".join(filter(None, [raw, url_slug])) or None
    return row.get("drivetrain") or detect_drivetrain(trim, model, combined_raw)


def resolve_drivetrain(listing: dict) -> str:
    """Public wrapper: return the effective drivetrain for a listing, applying all overrides."""
    model = (listing.get("model") or "").lower()
    trim  = listing.get("trim")
    raw   = (listing.get("raw") or {}).get("text") if isinstance(listing.get("raw"), dict) else None
    return _drivetrain_for(listing, trim, model, raw)


def _to_features(row: dict) -> list[float]:
    model = (row.get("model") or "").lower()
    trim  = row.get("trim")
    raw   = (row.get("raw") or {}).get("text") if isinstance(row.get("raw"), dict) else None
    
    if row.get("mileage") is None:
        vehicle_year = int(row.get("year") or _CURRENT_YEAR)
        age_years = max(1, _CURRENT_YEAR - vehicle_year + 1)
        raw_mileage = float(min(80_000, age_years * 12_000))
    else:
        raw_mileage = float(row.get("mileage"))
        
    return [
        float(row.get("year") or _CURRENT_YEAR),
        math.log1p(raw_mileage),
        _trim_premium(row),
        {"awd": 1.0, "fwd": 0.0, "unknown": 0.5}[_drivetrain_for(row, trim, model, raw)],
        {"electric": 1.0, "phev": 1.0, "hybrid": 0.8, "gas": 0.0}[
            row.get("powertrain") or detect_powertrain(trim, model, raw)],
        1.0 if _is_cpo(row) else 0.0,
    ]


# Feature order: [year, mileage, trim_premium, drivetrain, powertrain, cpo]
# Monotone constraints enforce economic common sense:
#   year (+1): newer = more expensive
#   mileage (-1): more miles = less expensive
#   trim_premium (+1): higher trim = more expensive
#   drivetrain (+1): AWD > FWD
#   powertrain (+1): hybrid/EV > gas
#   cpo (+1): CPO > non-CPO
_MONOTONIC_CST = [1, -1, 1, 1, 1, 1]  # year+, mileage-, trim_premium+, drivetrain+, powertrain+, cpo+


def _make_gb_pipeline() -> Pipeline:
    return Pipeline([
        ("scaler", StandardScaler()),
        ("gbr", HistGradientBoostingRegressor(
            max_iter=200, max_depth=4, learning_rate=0.05,
            min_samples_leaf=3, random_state=42,
            monotonic_cst=_MONOTONIC_CST,
        )),
    ])

_KNN_FEATURE_COLS = slice(0, 4)  # [year, log_mileage, trim_premium, drivetrain]


def _make_knn_pipeline(n: int) -> Pipeline:
    k = min(7, max(3, n // 10))
    return Pipeline([
        ("scaler", StandardScaler()),
        ("knn", KNeighborsRegressor(n_neighbors=k, weights="uniform")),
    ])


def _make_ridge_pipeline() -> Pipeline:
    return Pipeline([
        ("scaler", StandardScaler()),
        ("ridge", Ridge(alpha=200)),
    ])





# Models where older inventory is a different vehicle platform — exclude pre-platform rows
# so the model isn't contaminated by a fundamentally different car's depreciation curve.
# Keys are normalized (lowercase, no spaces/hyphens) for consistent lookups across all modules.
_PLATFORM_MIN_YEAR: dict[str, int] = {
    "tucsonhybrid":   2022,  # TNGA-K hybrid platform gen launched 2022
    "sportagehybrid": 2023,  # NQ5 platform launched 2023
    "sportagephev":   2023,  # PHEV also on NQ5 platform (launched 2023)
    "gv70":            2022,  # JX1 platform launched 2022 for US
    "tiguan":          2022,  # MK2 LCI refresh 2022 (new interior/features)
    "encoregx":        2024,  # 2024 refresh changed trims (Preferred/Sport Touring/Avenir); pre-2024 is different lineup
    "envision":        2021,  # 2nd gen launched 2021; 2020 is first gen
    "x1":              2023,  # U11 gen launched 2023; F48 (2016-2022) is a different car
    "x3":              2022,  # G01 LCI refresh 2022
    "xt4":             2024,  # target is 2024-2025 only; no earlier-gen data needed
    "nx350h":         2022,  # TNGA-K gen launched 2022; prior NX hybrids are different generation
    "xc40":            2023,  # CMA platform refresh; 2022 and earlier is a different gen
    "xc60":            2023,  # SPA platform refresh for 2023+
    "xc60recharge":   2023,  # Recharge T8 PHEV also on SPA refresh platform
    "q5":              2022,  # B9.5 refresh launched 2021; 2022+ is stable gen target
}


def load_training_data_for(model_name: str, powertrain_filter: str | None = None) -> tuple[list, list]:
    key = model_name.lower()
    norm_key = _norm(model_name)
    platform_min          = _PLATFORM_MIN_YEAR.get(norm_key, 0)
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

    params = [key, effective_min] + max_year_params + [_CURRENT_YEAR] + pt_params
    train_rows = fetch_all(f"""
        WITH price_bounds AS (
            SELECT cte.year,
                   PERCENTILE_CONT(0.10) WITHIN GROUP (ORDER BY cte.price)::int AS p10,
                   PERCENTILE_CONT(0.97) WITHIN GROUP (ORDER BY cte.price)::int AS p97
            FROM price_training_data cte
            WHERE lower(cte.model) = %s
              AND cte.price BETWEEN 20000 AND {max_price} AND cte.mileage BETWEEN 2000 AND 80000
              AND cte.year >= %s {cte_max_yr}
              AND (cte.year IS NULL OR cte.mileage::float / GREATEST(1, %s - cte.year + 1) <= 14000)
              {cte_pt_sql}
            GROUP BY cte.year
            HAVING count(*) >= 5
        ),
        global_bounds AS (
            SELECT PERCENTILE_CONT(0.10) WITHIN GROUP (ORDER BY cte.price)::int AS p10,
                   PERCENTILE_CONT(0.97) WITHIN GROUP (ORDER BY cte.price)::int AS p97
            FROM price_training_data cte
            WHERE lower(cte.model) = %s
              AND cte.price BETWEEN 20000 AND {max_price} AND cte.mileage BETWEEN 2000 AND 80000
              AND cte.year >= %s {cte_max_yr}
              AND (cte.year IS NULL OR cte.mileage::float / GREATEST(1, %s - cte.year + 1) <= 14000)
              {cte_pt_sql}
        )
        SELECT ptd.make, ptd.model, ptd.year, ptd.mileage, ptd.price, ptd.trim, ptd.powertrain, ptd.drivetrain, ptd.cpo
        FROM price_training_data ptd
        LEFT JOIN price_bounds pb ON ptd.year = pb.year
        CROSS JOIN global_bounds gb
        WHERE lower(ptd.model) = %s
          AND ptd.price IS NOT NULL AND ptd.mileage IS NOT NULL
          AND ptd.price BETWEEN 20000 AND {max_price} AND ptd.mileage BETWEEN 2000 AND 80000
          AND ptd.year >= %s {join_max_yr}
          AND ptd.mileage::float / GREATEST(1, %s - ptd.year + 1) <= 14000
          AND ptd.price BETWEEN COALESCE(pb.p10, gb.p10) AND COALESCE(pb.p97, gb.p97)
          {join_pt_sql}
    """, tuple(params + params + params))

    max_yr_sql = f"AND year <= {target_max}" if target_max else ""
    listing_rows = fetch_all(f"""
        SELECT make, model, year, mileage, price, trim, drivetrain,
               (insights->>'cpo')::boolean AS cpo, raw FROM listings
        WHERE price IS NOT NULL AND mileage IS NOT NULL
          AND condition = 'used' AND price BETWEEN 20000 AND {max_price}
          AND mileage BETWEEN 2000 AND 80000 AND lower(model) = %s
          AND year >= %s {max_yr_sql}
          AND status NOT IN ('new', 'active')
    """, (key, effective_min))
    rows = list(train_rows) + list(listing_rows)
    # For split models, filter listings by detected powertrain
    if powertrain_filter:
        rows = [
            r for r in rows
            if (r.get("powertrain") or detect_powertrain(r.get("trim"), r.get("model"))) == powertrain_filter
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


def _fit_stacked(model_name: str, X: list, y: list, label: str, pkl_path: str, save: bool) -> StackedModel | None:
    """Fit a stacked ensemble (GB + KNN + Ridge) with OOF meta-blend weights."""
    if len(X) < _MIN_ROWS:
        print(f"  [{label}] only {len(X)} rows — skipping (needs {_MIN_ROWS}+)")
        return None

    X_arr = np.array(X, dtype=float)
    y_arr = np.array(y, dtype=float)
    n     = len(X_arr)

    gb_pipe    = _make_gb_pipeline()
    knn_pipe   = _make_knn_pipeline(n)
    ridge_pipe = _make_ridge_pipeline()

    # OOF predictions for meta-learner (5 folds, min 2 folds for tiny datasets)
    n_splits = max(2, min(5, n // 6))
    kf  = KFold(n_splits=n_splits, shuffle=True, random_state=42)
    oof = np.zeros((n, 3))
    X_knn = X_arr[:, _KNN_FEATURE_COLS]
    for tr_idx, val_idx in kf.split(X_arr):
        X_tr, X_val = X_arr[tr_idx], X_arr[val_idx]
        y_tr = y_arr[tr_idx]
        gb_pipe.fit(X_tr, y_tr);                           oof[val_idx, 0] = gb_pipe.predict(X_val)
        knn_pipe.fit(X_knn[tr_idx], y_tr);                 oof[val_idx, 1] = knn_pipe.predict(X_knn[val_idx])
        ridge_pipe.fit(X_tr, y_tr);                        oof[val_idx, 2] = ridge_pipe.predict(X_val)

    # Meta Ridge: positive weights, no intercept → convex-ish blend
    meta = Ridge(alpha=10.0, fit_intercept=False, positive=True)
    meta.fit(oof, y_arr)
    raw_w = meta.coef_
    meta_weights = raw_w / raw_w.sum()

    # OOF MAE and residual std — honest estimates before full refit
    oof_stack = oof @ meta_weights
    oof_gb_mae    = np.mean(np.abs(oof[:, 0] - y_arr))
    oof_stack_mae = np.mean(np.abs(oof_stack  - y_arr))
    oof_residual_std = float(np.std(oof_stack - y_arr))

    # Refit base models on full data
    gb_pipe.fit(X_arr, y_arr)
    knn_pipe.fit(X_knn, y_arr)
    ridge_pipe.fit(X_arr, y_arr)

    stacked = StackedModel(gb=gb_pipe, knn=knn_pipe, ridge=ridge_pipe,
                           meta_weights=meta_weights, residual_std=oof_residual_std)

    w = meta_weights
    print(f"  [{label}] n={n}  OOF GB MAE=${oof_gb_mae:,.0f}  OOF Stack MAE=${oof_stack_mae:,.0f}"
          f"  w=[GB={w[0]:.2f} KNN={w[1]:.2f} Ridge={w[2]:.2f}]")

    if save:
        joblib.dump(stacked, pkl_path)
    return stacked


def train(save: bool = True) -> dict[str, Pipeline]:
    """Train one model per vehicle. Returns {model_name: pipeline}."""
    fitted: dict[str, Pipeline] = {}
    all_X, all_y = [], []

    for model_name in _ALL_MODELS:
        X, y = load_training_data_for(model_name)
        all_X.extend(X)
        all_y.extend(y)

        pipe = _fit_stacked(model_name, X, y, model_name, _pkl_path(model_name), save)
        if pipe:
            fitted[model_name] = pipe

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


def predict_fair_price(listing: dict) -> tuple[int, float] | tuple[None, None]:
    """Predict fair price using the per-model estimator.

    Returns (fair_price, residual_std) — both None if no model available.
    residual_std is the OOF residual std dev used to normalise the deal score.
    Returns (None, None) if mileage is unknown — can't reliably score without it.
    """
    if listing.get("mileage") is None:
        return None, None
    model_name = (listing.get("model") or "").lower().strip()
    features   = [_to_features(listing)]

    model = _load_model(_pkl_path(model_name))
    if model is not None:
        try:
            price = int(round(model.predict(features)[0]))
            std   = model.residual_std if isinstance(model, StackedModel) else 0.0
            return price, std
        except Exception:
            pass

    return None, None


_DRIVETRAIN_LABEL = {1.0: "AWD", 0.0: "FWD", 0.5: "Unknown drivetrain"}
_POWERTRAIN_LABEL = {1.0: "Electric/PHEV", 0.8: "Hybrid", 0.0: "Gas"}
_CPO_LABEL = {1.0: "Certified Pre-Owned (CPO)", 0.0: "Non-CPO"}


def explain_listing(listing: dict, kbb_price: int | None = None) -> dict | None:
    """Return SHAP-based price breakdown for a listing, including KBB Fair Price.

    Returns:
        {
          "base_value": 32000,          # average price for this model in training data
          "predicted": 34500,           # Blended fair price (70% ML + 30% KBB)
          "ml_pred": 34200,             # ML Stacked Model fair price
          "kbb_pred": 35200,            # KBB Fair Purchase Price
          "blended_pred": 34500,        # Blended fair price
          "ml_weight": 0.7,
          "kbb_weight": 0.3,
          "features": [ ... ]
        }
    """
    try:
        import shap as _shap
        import numpy as _np
    except ImportError:
        return None

    from tools.kbb_service import predict_kbb_fair_price

    model_name = (listing.get("model") or "").lower().strip()
    raw_features = _to_features(listing)
    X_raw = _np.array([raw_features])

    pipe = _load_model(_pkl_path(model_name))
    if pipe is None:
        return None

    try:
        gb_pipe = pipe.gb if isinstance(pipe, StackedModel) else pipe
        scaler = gb_pipe.named_steps["scaler"]
        gbr    = gb_pipe.named_steps["gbr"]
        X_scaled = scaler.transform(X_raw)

        explainer  = _shap.TreeExplainer(gbr)
        shap_vals  = explainer.shap_values(X_scaled)[0]
        ev         = explainer.expected_value
        base_value = float(_np.asarray(ev).flatten()[0])
        gb_pred_val = int(round(base_value + shap_vals.sum()))
        predicted  = int(round(pipe.predict(X_raw)[0])) if isinstance(pipe, StackedModel) else gb_pred_val

        # KBB Fair Price computation
        kbb_val = kbb_price if kbb_price is not None else predict_kbb_fair_price(listing)
        if kbb_val:
            blended_pred = int(round(0.70 * predicted + 0.30 * kbb_val))
            ml_w, kbb_w = 0.70, 0.30
        else:
            blended_pred = predicted
            ml_w, kbb_w = 1.0, 0.0

        gb_delta          = gb_pred_val - base_value
        target_delta      = predicted   - base_value
        ensemble_adj      = 0
        if abs(gb_delta) > 1e-2:
            scale_factor = target_delta / gb_delta
            if 0.3 <= scale_factor <= 3:
                shap_vals = shap_vals * scale_factor
            else:
                ensemble_adj = int(round(predicted - gb_pred_val))
        else:
            if abs(target_delta) > 1e-2:
                shap_vals = _np.array([target_delta / len(shap_vals)] * len(shap_vals))
            else:
                shap_vals = _np.zeros_like(shap_vals)

        year           = int(raw_features[0])
        if listing.get("mileage") is None:
            vehicle_year = int(listing.get("year") or _CURRENT_YEAR)
            age_years = max(1, _CURRENT_YEAR - vehicle_year + 1)
            mileage_actual = int(min(80_000, age_years * 12_000))
        else:
            mileage_actual = int(listing.get("mileage"))
        trim_lvl   = raw_features[2]
        drivetrain = raw_features[3]
        powertrain = raw_features[4]
        cpo        = raw_features[5]

        trim_ord = _trim_level(model_name, listing.get("trim"))
        trim_name = listing.get("trim") or f"Level {trim_ord:.0f}"

        raw_labels = [
            (f"Year {year}",                  float(year)),
            (f"{mileage_actual:,} miles",     float(mileage_actual)),
            (f"Trim — {trim_name[:25]}",      trim_lvl),
            (_DRIVETRAIN_LABEL.get(drivetrain, "Drivetrain"), drivetrain),
            (_POWERTRAIN_LABEL.get(powertrain, "Powertrain"), powertrain),
            (_CPO_LABEL.get(cpo, "CPO Status"), cpo),
        ]

        features = [
            {"label": label, "delta": int(round(float(sv))), "raw": float(raw)}
            for (label, raw), sv in zip(raw_labels, shap_vals)
            if abs(sv) >= 50
        ]
        if abs(ensemble_adj) >= 50:
            features.append({"label": "Ensemble Adjustment", "delta": ensemble_adj, "raw": float(ensemble_adj)})

        gb_pred = gb_pred_val
        if isinstance(pipe, StackedModel):
            knn_pred = int(round(pipe.knn.predict(X_raw[:, _KNN_FEATURE_COLS])[0]))
            ridge_pred = int(round(pipe.ridge.predict(X_raw)[0]))
            meta_weights = [float(w) for w in pipe.meta_weights]
            residual_std = float(pipe.residual_std)
        else:
            knn_pred = None
            ridge_pred = None
            meta_weights = [1.0, 0.0, 0.0]
            residual_std = 0.0

        return {
            "base_value": int(round(base_value)),
            "predicted":  blended_pred,
            "ml_pred":    predicted,
            "kbb_pred":   kbb_val,
            "blended_pred": blended_pred,
            "ml_weight":  ml_w,
            "kbb_weight": kbb_w,
            "features":   features,
            "gb_pred":    gb_pred,
            "knn_pred":   knn_pred,
            "ridge_pred": ridge_pred,
            "meta_weights": meta_weights,
            "residual_std": residual_std,
        }
    except Exception:
        return None


if __name__ == "__main__":
    print("Training per-model price estimators...")
    train()
    print("Done.")
