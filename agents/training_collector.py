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
Re-runnable — ON CONFLICT updates trim/drivetrain/powertrain so fixes to canonical_trim propagate.
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env.local"))

# Force line-buffered stdout so output appears immediately when piped to tee/files
sys.stdout.reconfigure(line_buffering=True)

from tools.log_tee import enable_log_tee
from tools.db import execute, fetch_one, refresh_self_computed_medians
from tools.base_trims import get_allowed_makes, get_allowed_models, get_vehicles, _norm, is_trim_allowed, is_trim_allowed_for_training, canonical_trim, get_model_min_year, get_model_max_year, normalize_listing_model, _VEHICLES
from tools.scoring import detect_powertrain, detect_drivetrain
from tools.price_model import _drivetrain_for
from tools.scrapers import scrape_cargurus, scrape_carvana, scrape_carscom, scrape_carmax
from tools.dealer_scraper import scrape_all_dealers
from tools.job_tracker import start_job, update_job, complete_job
from tools.price_model import _PLATFORM_MIN_YEAR, _is_cpo

ZIP         = "30328"   # anchor zip (CarGurus requires one)
RADIUS      = 50000     # nationwide (CarGurus treats large values as no-distance-limit)
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


def _insert(rows: list[dict], allowed_norm: set[str], min_year: int | None = None, max_year: int | None = None) -> int:
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
        make, model = normalize_listing_model(make, model, l.get("trim"))
        # Check allowlist — treat "xc60recharge" as "xc60" since they share a CarGurus page
        check_norm = _norm(model)
        if check_norm == "xc60recharge":
            check_norm = "xc60"
        if check_norm not in allowed_norm:
            continue

        raw_text_base = (l.get("raw") or {}).get("text") if isinstance(l.get("raw"), dict) else None
        url_slug = (l.get("listing_url") or "").replace("-", " ").replace("/", " ")
        raw_text = " ".join(filter(None, [raw_text_base, url_slug])) or None

        # Re-check allowlist after normalize_listing_model (XC60 split) to filter out gas XC60
        if _norm(model) not in allowed_norm:
            continue
        if min_year and int(year) < min_year:
            continue
        if max_year and int(year) > max_year:
            continue
        if price < 20000:
            continue  # exclude lemon/salvage/fleet-priced cars
        if not miles or miles < 2000:
            continue  # exclude near-new dealer inventory (listed as "used" but priced at MSRP)
        years_old = _CURR_YEAR - int(year) + 1
        if miles and year and years_old > 0 and (miles / years_old) > 14000:
            continue  # >14k miles/year likely rental/fleet usage
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
        # Detect powertrain and drivetrain from original trim before canonicalization strips
        # model-specific prefixes (e.g. "Recharge T8 Plus Dark Theme eAWD" → phev/awd, trim="Plus")
        raw_trim = l.get("trim")
        powertrain = detect_powertrain(raw_trim, model, raw_text)
        # Use _drivetrain_for so _DRIVETRAIN_DEFAULTS model-year overrides apply at insert time
        # (e.g. 2024+ XC40 → always AWD even when canonical trim "Plus" has no AWD keyword).
        drivetrain = _drivetrain_for({"make": make, "model": model, "year": year}, raw_trim, model.lower(), raw_text)
        # Validate raw trim against allowlist first (same order as sourcing_agent.py),
        # then canonicalize — avoids accepting a trim only because canonical_trim mangled it.
        if not is_trim_allowed_for_training(make, model, raw_trim, source=source):
            continue
        trim = canonical_trim(make, model, raw_trim)
        if not _is_clean(l, source):
            continue  # accident or bad title — same gates as active listings
        cpo = _is_cpo(l)
        try:
            execute(
                """INSERT INTO price_training_data
                   (make, model, year, mileage, price, condition, trim, source, listing_url, powertrain, drivetrain, cpo)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (source, listing_url)
                   DO UPDATE SET drivetrain = EXCLUDED.drivetrain,
                                 powertrain = EXCLUDED.powertrain,
                                 trim       = EXCLUDED.trim,
                                 price      = EXCLUDED.price,
                                 mileage    = EXCLUDED.mileage,
                                 cpo        = EXCLUDED.cpo""",
                (make, model, year, miles, price,
                 l.get("condition", "used"), trim, source,
                 l.get("listing_url") or l.get("url"), powertrain, drivetrain, cpo),
            )
            inserted += 1
        except Exception:
            continue
    return inserted


_SCRAPER_TIMEOUT = 900  # seconds per scraper before giving up (15 min covers all-model CarGurus runs)


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


_CPO_SOURCES       = {"carvana", "carmax"}   # inventory focused on CPO/newer cars
_HIGH_MILEAGE_SKIP = 40_000                  # skip CPO sources above this mileage cap


def run_stage(desc, min_year, max_mileage, max_price, makes, allowed_norm, vehicles,
              cg_scraped: set | None = None, max_year: int | None = None) -> int:
    """Run all four scrapers for one stage group.

    cg_scraped (optional) — mutable set of (eff_min, frozenset(vehicles)) keys for
    CarGurus dedup across stages. CarGurus URL has no mileage/price filter so
    re-running it for the same year+model group fetches identical data.
    When provided, CarGurus is skipped if this group was already scraped.
    """
    eff_max = max_year if max_year is not None else MAX_YEAR
    scrapers = [
        ("cargurus", scrape_cargurus, {"radius": RADIUS, "max_year": eff_max, "clean_history": True, "local": False, "vehicles": vehicles}),
        ("carscom",  scrape_carscom,  {"radius": RADIUS, "vehicles": vehicles, "clean_history": True, "max_year": eff_max}),
        ("carvana",  scrape_carvana,  {"vehicles": vehicles, "max_year": eff_max}),
        ("carmax",   scrape_carmax,   {"radius": RADIUS, "vehicles": vehicles, "max_year": eff_max}),
    ]
    total = 0
    cg_key = (min_year, frozenset(vehicles)) if cg_scraped is not None else None

    for label, fn, kwargs in scrapers:
        # CarGurus dedup: same (eff_min, vehicles) group produces identical URLs across stages
        if label == "cargurus" and cg_scraped is not None:
            if cg_key in cg_scraped:
                print(f"    [{_ts()}] cargurus: skipped (same year/model group already scraped)", flush=True)
                continue
            cg_scraped.add(cg_key)

        # CPO sources (Carvana/CarMax) rarely have high-mileage cars — skip to save time
        if label in _CPO_SOURCES and max_mileage > _HIGH_MILEAGE_SKIP:
            print(f"    [{_ts()}] {label}: skipped (max_mileage={max_mileage:,} > {_HIGH_MILEAGE_SKIP:,}, CPO source)", flush=True)
            continue

        print(f"    [{_ts()}] {label}: starting...", flush=True)
        t0 = time.time()
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(fn, ZIP, makes, max_price, max_mileage, min_year, **kwargs)
                rows = future.result(timeout=_SCRAPER_TIMEOUT)
            n = _insert(rows, allowed_norm, min_year=min_year, max_year=eff_max)
            total += n
            elapsed = time.time() - t0
            print(f"    [{_ts()}] {label}: {len(rows)} scraped → {n} new rows  ({elapsed:.0f}s)", flush=True)
        except FuturesTimeout:
            elapsed = time.time() - t0
            print(f"    [{_ts()}] {label}: TIMEOUT after {elapsed:.0f}s — skipping", flush=True)
        except Exception as e:
            elapsed = time.time() - t0
            print(f"    [{_ts()}] {label}: ERROR after {elapsed:.0f}s — {e}", flush=True)
    return total


def _stage_groups(all_vehicles: list[tuple[str, str]], stage_min_year: int) -> dict[int, list[tuple[str, str]]]:
    """Group vehicles by effective min_year for a given stage.

    Each group gets its own scraper call with an exact min_year so we never
    fetch listings below a model's floor. Models whose floor is above the stage
    min_year are skipped entirely (e.g. Sportage Hybrid min=2026 is skipped in
    a 2023 stage — there are no valid listings to collect).

    Returns {effective_min_year: [(make, model), ...]} sorted ascending.
    Models above their max_year ceiling are excluded entirely.
    """
    groups: dict[int, list[tuple[str, str]]] = {}
    for make, model in all_vehicles:
        model_max = get_model_max_year(make, model)
        if model_max and stage_min_year > model_max:
            continue  # stage starts above this model's ceiling — skip
        model_min = max(
            get_model_min_year(make, model) or 0,
            _PLATFORM_MIN_YEAR.get(_norm(model), 0),
        )
        if stage_min_year < model_min:
            continue  # stage starts below this model's floor — skip
        eff_min = max(stage_min_year, model_min)
        groups.setdefault(eff_min, []).append((make, model))
    return dict(sorted(groups.items()))


def main(broad: bool = False):
    enable_log_tee()  # tee all output to /tmp/carhunt-refresh.log for the dashboard SSE stream

    all_vehicles     = get_vehicles()
    all_allowed_norm = {_norm(model) for _, model in all_vehicles}
    stages           = STAGES_BROAD if broad else STAGES
    mode_label       = "BROAD (2023-2026, no price cap)" if broad else "standard (2025-2026)"

    print(f"\n{'='*62}", flush=True)
    print(f"  Market data collection — {mode_label}", flush=True)
    print(f"  zip {ZIP}, radius {RADIUS} mi (nationwide)", flush=True)
    print(f"  {len(stages)} stages  |  {len(all_vehicles)} models", flush=True)
    print(f"  Enrichment: CarGurus + Cars.com (accident/title validation)", flush=True)
    print(f"{'='*62}\n", flush=True)

    before      = int(fetch_one("SELECT count(*) AS n FROM price_training_data")["n"])
    grand_total = 0
    job_id      = start_job("retrain", stage_total=len(stages))
    # CarGurus dedup set: tracks (eff_min, frozenset(vehicles)) already scraped.
    # CarGurus URL has no mileage/price params, so repeated stage calls for the same
    # year+model group return identical listings — pure wasted time.
    cg_scraped: set[tuple] = set()

    try:
        for i, (desc, min_year, max_mileage, max_price) in enumerate(stages, 1):
            groups = _stage_groups(all_vehicles, min_year)
            if not groups:
                print(f"[{i:02d}/{len(stages)}] {desc}  — skipped (no models in range)\n", flush=True)
                continue
            price_label = "no cap" if max_price >= 999_999 else f"${max_price:,}"
            print(f"[{i:02d}/{len(stages)}] {desc}  (mi<={max_mileage:,}, price<={price_label})", flush=True)
            update_job(job_id, stage_current=i, stage_label=desc, rows_added=grand_total)
            stage_total_rows = 0
            for eff_min, group_vehicles in groups.items():
                makes        = list({make for make, _ in group_vehicles})
                allowed_norm = {_norm(model) for _, model in group_vehicles}
                model_names  = ", ".join(model for _, model in group_vehicles)
                print(f"  [yr>={eff_min}] {model_names}", flush=True)
                n = run_stage(desc, eff_min, max_mileage, max_price, makes, allowed_norm, group_vehicles,
                              cg_scraped=cg_scraped)
                stage_total_rows += n
            grand_total += stage_total_rows

            if i % 3 == 0:
                cur = int(fetch_one("SELECT count(*) AS n FROM price_training_data")["n"])
                print(f"  ↳ running total: {cur} rows\n", flush=True)
            else:
                print(flush=True)

        print("Scraping dealer sites...", flush=True)
        update_job(job_id, stage_current=len(stages), stage_label="Dealer scrape", rows_added=grand_total)
        try:
            all_makes = list({make for make, _ in all_vehicles})
            dealer_rows = scrape_all_dealers(makes=all_makes, force=True, update_cooldown=False)
            n = _insert(dealer_rows, all_allowed_norm)
            grand_total += n
            print(f"    dealers: {len(dealer_rows)} scraped → {n} new rows\n", flush=True)
        except Exception as e:
            print(f"    dealers: ERROR — {e}\n", flush=True)

        print("Recomputing market price medians...", flush=True)
        update_job(job_id, stage_current=len(stages), stage_label="Recomputing medians", rows_added=grand_total)
        cached = refresh_self_computed_medians()

        after = int(fetch_one("SELECT count(*) AS n FROM price_training_data")["n"])
        complete_job(job_id)
    except Exception:
        complete_job(job_id, status="error")
        raise
    print(f"\n{'='*62}", flush=True)
    print(f"  Done.", flush=True)
    print(f"  New rows collected : {grand_total}", flush=True)
    print(f"  Total training rows: {after}  (was {before})", flush=True)
    print(f"  Cache entries      : {cached} make/model/year/bucket combos", flush=True)
    print(f"{'='*62}\n", flush=True)


if __name__ == "__main__":
    broad = "--broad" in sys.argv
    main(broad=broad)
