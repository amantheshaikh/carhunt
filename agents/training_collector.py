"""
Staged market data collector — nationwide pricing.
Sources: CarGurus, Cars.com, Carvana, CarMax (all four working scrapers).
Varies year range and mileage cap per stage to cover all price buckets.

CarGurus and Cars.com listings are enriched (detail-page fetch) before insert
so that accident and title data is checked — same heuristics as active listings.
CarMax and Carvana screen their own inventory so no enrichment is needed.

Run:
  python3 training_collector.py           # standard (2025-2026, ≤$55k)
  python3 training_collector.py --broad   # extended (2023-2026, no price cap, more stages)
Re-runnable — ON CONFLICT DO NOTHING prevents duplicates.
"""
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env.local"))

from tools.db import execute, fetch_one, refresh_self_computed_medians
from tools.base_trims import get_allowed_makes, get_allowed_models, get_vehicles, _norm, is_trim_allowed, canonical_trim, get_model_min_year, get_model_max_year
from tools.scoring import detect_powertrain
from tools.scrapers import scrape_cargurus, scrape_carvana, scrape_carscom, scrape_carmax, scrape_cargurus_search
from tools.base_trims import _VEHICLES as _ALL_VEHICLES

# Vehicles with targeted CarGurus search paths (trim-level + full history filters)
_CARGURUS_SEARCH_PATHS = {
    (v["make"].lower(), v["model"].lower()): {
        "path": v["cargurus_search_path"],
        "min_year": v.get("min_year", 2020),
        "max_year": v.get("max_year"),
    }
    for v in _ALL_VEHICLES if v.get("cargurus_search_path")
}
from tools.dealer_scraper import scrape_all_dealers
from tools.job_tracker import start_job, update_job, complete_job
from tools.price_model import _PLATFORM_MIN_YEAR

ZIP         = "30328"   # anchor zip (CarGurus requires one; 2500 mi = continental US)
RADIUS      = 2500      # nationwide
MAX_YEAR    = datetime.now().year
_CURR_YEAR  = datetime.now().year

# Sources that require detail-page enrichment to get accident/title data.
# CarMax and Carvana screen their own inventory — no enrichment needed.
_ENRICHMENT_REQUIRED = {"cargurus", "carscom"}

# Standard stages — recent inventory, conservative price cap
STAGES = [
    # (description, min_year, max_mileage, max_price)
    ("0-10k  | 2026",        2026, 10_000, 55_000),
    ("0-10k  | 2025",        2025, 10_000, 55_000),
    ("10-20k | 2026",        2026, 20_000, 55_000),
    ("10-20k | 2025",        2025, 20_000, 55_000),
    ("20-30k | 2025-2026",   2025, 30_000, 55_000),
    ("broad  | 2026, 40k",   2026, 40_000, 55_000),
    ("broad  | 2025, 50k",   2025, 50_000, 50_000),
    ("broad  | 2025, 80k",   2025, 80_000, 45_000),
]

# Broad stages — wider year range + no price ceiling for richer ML training data
STAGES_BROAD = STAGES + [
    # 2024 models — more inventory, better mileage/depreciation coverage
    ("0-10k  | 2024",        2024, 10_000, 60_000),
    ("10-20k | 2024",        2024, 20_000, 55_000),
    ("20-30k | 2024",        2024, 30_000, 50_000),
    ("30-50k | 2024",        2024, 50_000, 45_000),
    # 2023 — fills out the depreciation curve
    ("0-30k  | 2023",        2023, 30_000, 55_000),
    ("30-60k | 2023",        2023, 60_000, 40_000),
    # High trim / CPO territory — no price cap, captures Limited/Prestige pricing
    ("high-trim | 2025-2026 no cap", 2025, 20_000, 999_999),
    ("high-trim | 2024 no cap",      2024, 20_000, 999_999),
    # Lexus NX 350h coverage — priced $42-65k, missed by most stages above
    ("lexus nx | 2022-2025, all mi", 2022, 80_000, 999_999),
]


def _is_clean(listing: dict, source: str) -> bool:
    """Apply same quality gates as active-listing validation."""
    # Reject accidents
    accidents = listing.get("accident_count")
    if accidents is not None and accidents > 0:
        return False
    # Reject bad title status
    title = (listing.get("title_status") or "unknown").lower()
    if title in ("salvage", "rebuilt", "flood", "lemon", "fleet"):
        return False
    # For enrichment-required sources, reject if enrichment never ran (accident_count still None)
    if source in _ENRICHMENT_REQUIRED and accidents is None:
        return False
    return True


def _insert(rows: list[dict], allowed_norm: set[str]) -> int:
    inserted = 0
    for l in rows:
        make  = l.get("make", "")
        model = l.get("model", "")
        year  = l.get("year")
        miles = l.get("mileage")
        price = l.get("price")
        source = l.get("source", "cargurus")
        if not all([make, model, year, price]):
            continue
        if price < 20000:
            continue  # exclude lemon/salvage/fleet-priced cars
        if miles and year and (miles / (_CURR_YEAR - int(year) + 1)) > 14000:
            continue  # >14k miles/year likely rental/fleet usage
        if _norm(model) not in allowed_norm:
            continue
        # Enforce year range: use the stricter of platform min and cars.json target min
        platform_min = _PLATFORM_MIN_YEAR.get(_norm(model), 0)
        target_min   = get_model_min_year(make, model) or 0
        effective_min = max(platform_min, target_min)
        if effective_min and int(year) < effective_min:
            continue
        # Enforce cars.json max year — don't collect years we don't target
        target_max = get_model_max_year(make, model)
        if target_max and int(year) > target_max:
            continue
        # Detect powertrain from original trim before canonicalization strips model-specific prefixes
        # (e.g. "Recharge T8 Plus Dark Theme eAWD" → powertrain=phev, trim="Plus")
        raw_trim = l.get("trim")
        powertrain = detect_powertrain(raw_trim, model, (l.get("raw") or {}).get("text") if isinstance(l.get("raw"), dict) else None)
        # Enforce trim allowlist — reject M35i, M40i, M50 etc. that skew fair-value model
        trim = canonical_trim(make, model, raw_trim)
        if not is_trim_allowed(make, model, trim):
            continue
        if not _is_clean(l, source):
            continue  # accident or bad title — same gates as active listings
        try:
            execute(
                """INSERT INTO price_training_data
                   (make, model, year, mileage, price, condition, trim, source, listing_url, powertrain)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (source, make, model, year, mileage, price) DO NOTHING""",
                (make, model, year, miles, price,
                 l.get("condition", "used"), trim, source,
                 l.get("listing_url") or l.get("url"), powertrain),
            )
            inserted += 1
        except Exception:
            continue
    return inserted


def run_stage(desc, min_year, max_mileage, max_price, makes, allowed_norm, vehicles) -> int:
    total = 0
    scrapers = [
        # CarGurus: new search format for search-path models (local=False = nationwide, no GA filter)
        (scrape_cargurus, "cargurus", {"radius": RADIUS, "max_year": MAX_YEAR, "clean_history": True, "local": False}),
        (scrape_carscom,  "carscom",  {"radius": RADIUS, "vehicles": vehicles, "clean_history": True}),
        (scrape_carvana,  "carvana",  {"vehicles": vehicles}),
        (scrape_carmax,   "carmax",   {"radius": RADIUS, "vehicles": vehicles}),
    ]
    for fn, label, kwargs in scrapers:
        try:
            rows = fn(ZIP, makes, max_price, max_mileage, min_year, **kwargs)
            n = _insert(rows, allowed_norm)
            total += n
            print(f"    {label}: {len(rows)} scraped → {n} new rows")
        except Exception as e:
            print(f"    {label}: ERROR — {e}")
    return total


def main(broad: bool = False):
    makes        = get_allowed_makes()
    vehicles     = get_vehicles()
    allowed_norm = {_norm(m) for m in get_allowed_models()}
    stages       = STAGES_BROAD if broad else STAGES
    mode_label   = "BROAD (2023-2026, no price cap)" if broad else "standard (2025-2026)"

    print(f"\n{'='*62}")
    print(f"  Market data collection — {mode_label}")
    print(f"  zip {ZIP}, radius {RADIUS} mi (nationwide)")
    print(f"  {len(stages)} stages  |  {len(allowed_norm)} models  |  {len(makes)} makes")
    print(f"  Enrichment: CarGurus + Cars.com (accident/title validation)")
    print(f"{'='*62}\n")

    before      = int(fetch_one("SELECT count(*) AS n FROM price_training_data")["n"])
    grand_total = 0
    job_id      = start_job("retrain", stage_total=len(stages))

    try:
        for i, (desc, min_year, max_mileage, max_price) in enumerate(stages, 1):
            price_label = "no cap" if max_price >= 999_999 else f"${max_price:,}"
            print(f"[{i:02d}/{len(stages)}] {desc}  "
                  f"(yr>={min_year}, mi<={max_mileage:,}, price<={price_label})")
            update_job(job_id, stage_current=i, stage_label=desc, rows_added=grand_total)
            n = run_stage(desc, min_year, max_mileage, max_price, makes, allowed_norm, vehicles)
            grand_total += n

            if i % 3 == 0:
                cur = int(fetch_one("SELECT count(*) AS n FROM price_training_data")["n"])
                print(f"  ↳ running total: {cur} rows\n")
            else:
                print()

        print("Scraping dealer sites...")
        update_job(job_id, stage_current=len(stages), stage_label="Dealer scrape", rows_added=grand_total)
        try:
            dealer_rows = scrape_all_dealers(force=True)
            n = _insert(dealer_rows, allowed_norm)
            grand_total += n
            print(f"    dealers: {len(dealer_rows)} scraped → {n} new rows\n")
        except Exception as e:
            print(f"    dealers: ERROR — {e}\n")

        print("Recomputing market price medians...")
        update_job(job_id, stage_current=len(stages), stage_label="Recomputing medians", rows_added=grand_total)
        cached = refresh_self_computed_medians()

        after = int(fetch_one("SELECT count(*) AS n FROM price_training_data")["n"])
        complete_job(job_id)
    except Exception:
        complete_job(job_id, status="error")
        raise
    print(f"\n{'='*62}")
    print(f"  Done.")
    print(f"  New rows collected : {grand_total}")
    print(f"  Total training rows: {after}  (was {before})")
    print(f"  Cache entries      : {cached} make/model/year/bucket combos")
    print(f"{'='*62}\n")


if __name__ == "__main__":
    broad = "--broad" in sys.argv
    main(broad=broad)
