"""
Sourcing Agent — scrapes all sources, diffs against DB, enriches new listings.

Per-source flow:
  1. Scrape: collect all visible listings from the source.
  2. Diff: new listings → enrich with detail-page history → validate → INSERT.
         existing listings → update price if changed.
  3. 30-day global expiry runs at the end of each full refresh.

Sources: CarGurus, Cars.com, CarMax, Carvana, dealer sites.
(AutoTrader blocked by Akamai; Edmunds blocked at network level.)
"""
import json
import os
import re
import time
import traceback
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env.local"))

from psycopg2.errors import UniqueViolation
from tools.db import fetch_one, fetch_all, execute, get_conn
from tools.scrapers import (
    scrape_cargurus, scrape_carmax, scrape_carvana,
    scrape_carscom, enrich_listings_from_detail,
)
from tools.dealer_scraper import scrape_all_dealers
from tools.base_trims import get_allowed_makes, get_allowed_models, get_vehicles, is_trim_allowed, get_model_min_year, get_model_max_year, get_trim_min_year, get_trim_max_year, trim_belongs_to_different_model, canonical_trim, _VEHICLES, _norm

# Canonical make/model names from cars.json — used to normalise scraper-specific casing
_CANONICAL_NAMES: dict[tuple[str, str], tuple[str, str]] = {
    (v["make"].lower(), v["model"].lower()): (v["make"], v["model"])
    for v in _VEHICLES
}

# Skip a source if it was successfully scraped within this many hours
MIN_HOURS_BETWEEN_RUNS = 6

SCRAPERS = {
    "cargurus":   scrape_cargurus,
    "carscom":    scrape_carscom,
    "carmax":     scrape_carmax,
    "carvana":    scrape_carvana,
}

DEALER_SOURCE = "dealer"


_FORCE_MODE = False  # set to True by run(..., force=True) to bypass cooldown without DB changes


def _should_skip_source(source: str) -> tuple[bool, str]:
    """Return (skip, reason). Skip if recently scraped with active results."""
    if _FORCE_MODE:
        return False, "force mode"
    row = fetch_one(
        """SELECT completed_at, source_results, source_errors
           FROM refresh_log
           WHERE completed_at IS NOT NULL
             AND source_results IS NOT NULL
           ORDER BY completed_at DESC
           LIMIT 1"""
    )
    if not row:
        return False, "no previous run"

    completed_at = row["completed_at"]
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - completed_at).total_seconds() / 3600

    if age_hours > MIN_HOURS_BETWEEN_RUNS:
        return False, f"last run was {age_hours:.1f}h ago"

    source_results = row.get("source_results") or {}
    source_errors = row.get("source_errors") or {}

    if source_errors and source_errors.get(source):
        return False, f"last run had error for {source}"

    prev_count = source_results.get(source, 0)
    active_count = fetch_one(
        "SELECT count(*) AS n FROM listings WHERE source = %s AND status NOT IN ('dismissed','purchased')",
        (source,)
    )["n"]

    if prev_count > 0 and active_count > 0:
        return True, f"scraped {age_hours:.1f}h ago, {active_count} active listings still fresh"

    return False, "no active listings from this source"


def _get_known_ids(source: str) -> dict[str, int]:
    """Return {external_id: price} for all non-expired listings from this source."""
    rows = fetch_all(
        "SELECT external_id, price FROM listings WHERE source = %s AND status NOT IN ('dismissed')",
        (source,)
    )
    return {r["external_id"]: r["price"] for r in rows}


def _get_known_urls_and_vins() -> tuple[set[str], set[str]]:
    """Return (known_urls, known_vins) in one query — called once per run."""
    rows = fetch_all(
        "SELECT listing_url, vin FROM listings"
        " WHERE status NOT IN ('dismissed')"
        " AND (listing_url IS NOT NULL OR vin IS NOT NULL)"
    )
    urls = {r["listing_url"] for r in rows if r["listing_url"]}
    vins = {r["vin"] for r in rows if r["vin"]}
    return urls, vins


def _get_known_fingerprints(exclude_source: str) -> set[tuple]:
    """Return (year, mileage, price) tuples for non-expired listings from OTHER sources.
    Catches cross-source duplicates when the new listing has no VIN (e.g. CarGurus blades)."""
    rows = fetch_all(
        "SELECT year, mileage, price FROM listings "
        "WHERE source != %s AND status NOT IN ('dismissed') "
        "AND year IS NOT NULL AND mileage IS NOT NULL AND price IS NOT NULL",
        (exclude_source,)
    )
    return {(r["year"], r["mileage"], r["price"]) for r in rows}


_normalize_model = _norm  # identical to base_trims._norm; alias avoids duplication


def _is_valid(listing: dict, min_year: int, allowed_makes: list[str],
              allowed_models: list[str] | None = None, max_year: int | None = None,
              max_price: int | None = None, max_mileage: int | None = None,
              max_distance: int | None = None) -> bool:
    title_status = (listing.get("title_status") or "unknown").lower()
    if title_status in ("salvage", "rebuilt", "flood", "lemon", "fleet"):
        return False
    accidents = listing.get("accident_count")
    if accidents is not None and accidents > 0:
        return False
    make  = listing.get("make", "")
    model = listing.get("model", "")
    year  = listing.get("year")
    price = listing.get("price") or 0
    if not price or price < 8000 or not make or not year:
        return False
    if max_price and price > max_price:
        return False
    # Enforce radius: CarGurus sometimes returns listings beyond the search radius,
    # including "Home delivery" listings with no distance shown — fall back to state check.
    distance = listing.get("distance_miles")
    if max_distance:
        if distance is not None and distance > max_distance:
            return False
        elif distance is None:
            # Home delivery / store transfer listings have no distance.
            # Reject "Store transfer to X" listings — physical car is elsewhere.
            dealer_name = listing.get("dealer_name") or ""
            if re.search(r"store transfer", dealer_name, re.IGNORECASE):
                return False
            # For plain home-delivery listings: parse state from dealer_name (e.g. "Wall Township, NJ")
            # Only accept GA — other "nearby" states (NC, FL, MS) can still be 300-500mi away.
            state_m = re.search(r",\s*([A-Z]{2})$", dealer_name.strip())
            if state_m:
                if state_m.group(1) != "GA":
                    return False
    mileage = listing.get("mileage")
    if listing.get("condition", "used") == "used":
        # Mileage is now extracted from the detail page for dealer listings,
        # so null mileage after enrichment is OK — the max_mileage check handles overages.
        if max_mileage and mileage is not None and mileage > max_mileage:
            return False
    trim = listing.get("trim")
    if not is_trim_allowed(make, model, trim):
        return False
    if trim_belongs_to_different_model(make, model, trim):
        return False  # cross-model data contamination (e.g. "SEL Convenience" on a Sportage)
    if allowed_makes and make.lower() not in {m.lower() for m in allowed_makes}:
        return False
    if allowed_models:
        normalized = _normalize_model(model)
        if not any(_normalize_model(am) == normalized for am in allowed_models):
            return False
    if year < min_year:
        return False
    if max_year and year > max_year:
        return False
    # Per-model year overrides
    model_min = get_model_min_year(make, model)
    if model_min and year < model_min:
        return False
    model_max = get_model_max_year(make, model)
    if model_max and year > model_max:
        return False
    # Per-trim year override (e.g. Sportage EX requires 2026+)
    trim_min = get_trim_min_year(make, model, trim)
    if trim_min and year < trim_min:
        return False
    # Per-trim max year (e.g. Tucson Hybrid SEL/N Line not offered in 2026+)
    trim_max = get_trim_max_year(make, model, trim)
    if trim_max and year > trim_max:
        return False
    return True


def _save_new(listing: dict) -> None:
    # Normalise make/model to canonical casing from cars.json (scrapers use ALL CAPS, title case, etc.)
    mk, mo = listing["make"], listing["model"]
    canonical = _CANONICAL_NAMES.get((mk.lower(), mo.lower()))
    if canonical:
        mk, mo = canonical

    # Normalize trim to canonical allowlist value (strips suffixes like "AWD", "Sport Activity Vehicle")
    trim = canonical_trim(mk, mo, listing.get("trim"))

    # Skip if the VIN is already tracked as an active listing from any source.
    # The WHERE NOT EXISTS handles the common case; the unique index is the hard backstop.
    insights = listing.get("insights")
    try:
        execute(
            """INSERT INTO listings
               (source, external_id, vin, make, model, trim, year, body_type, mileage, price,
                dealer_name, dealer_phone, dealer_email, listing_url, photos_url,
                title_status, accident_count, condition, status, insights, raw)
               SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'new',%s,%s
               WHERE NOT EXISTS (
                   SELECT 1 FROM listings
                   WHERE vin = %s AND vin IS NOT NULL
                   AND status NOT IN ('dismissed', 'purchased')
               )
               ON CONFLICT (source, external_id) DO NOTHING""",
            (
                listing["source"], str(listing["external_id"]), listing.get("vin"),
                mk, mo, trim, listing["year"],
                listing.get("body_type"), listing.get("mileage"), listing["price"],
                listing.get("dealer_name"), listing.get("dealer_phone"), listing.get("dealer_email"),
                listing["listing_url"], listing.get("photos_url"),
                listing.get("title_status"), listing.get("accident_count"),
                listing.get("condition", "used"),
                json.dumps(insights) if insights else None,
                json.dumps(listing.get("raw", {})),
                listing.get("vin"),  # for the WHERE NOT EXISTS vin check
            ),
        )
    except UniqueViolation:
        pass  # concurrent refresh inserted the same VIN first; unique index caught it


def _touch_existing(source: str, external_id: str, new_price: int, old_price: int) -> bool:
    """Update last_seen_at and price atomically. Returns True if price changed."""
    price_changed = new_price != old_price
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE listings SET last_seen_at = now(), price = %s WHERE source = %s AND external_id = %s",
                (new_price, source, external_id),
            )
            if price_changed:
                cur.execute(
                    "DELETE FROM listing_scores WHERE listing_id = "
                    "(SELECT id FROM listings WHERE source=%s AND external_id=%s)",
                    (source, external_id),
                )
        conn.commit()
    return price_changed


def process_source(
    source: str,
    fn,
    zip_code: str,
    makes: list,
    max_price: int,
    max_mileage: int,
    min_year: int,
    allowed_models: list[str] | None = None,
    max_year: int | None = None,
    vehicles: list | None = None,
    radius: int = 100,
    known_urls: set | None = None,
    known_vins: set | None = None,
) -> dict:
    """Scrape one source and intelligently diff against DB. Returns stats dict.

    known_urls / known_vins are cross-source dedup sets shared across all sources
    in a single run. Fetched from DB lazily if not supplied (backward-compat).
    Both sets are mutated in-place as new listings are saved.
    """
    skip, reason = _should_skip_source(source)
    if skip:
        print(f"  [{source}] SKIP — {reason}")
        return {"source": source, "skipped": True, "reason": reason, "found": 0, "new": 0, "updated": 0, "errors": None}

    print(f"  [{source}] Scraping (radius={radius} mi)...")
    kwargs: dict = {}
    if max_year and source.startswith("cargurus"):
        kwargs["max_year"] = max_year
    if source in ("cargurus", "carmax", "carscom"):
        kwargs["radius"] = radius
    if vehicles and not source.startswith("cargurus"):
        kwargs["vehicles"] = vehicles

    listings = []
    last_err = None
    for attempt in range(1, 4):  # up to 3 attempts with exponential backoff
        try:
            listings = fn(zip_code, makes, max_price, max_mileage, min_year, **kwargs)
            break
        except Exception as e:
            last_err = e
            wait = 2 ** attempt
            print(f"  [{source}] attempt {attempt} failed: {e}  (retry in {wait}s)")
            if attempt < 3:
                time.sleep(wait)
    else:
        print(f"  [{source}] ERROR after 3 attempts — {last_err}")
        traceback.print_exc()
        return {"source": source, "skipped": False, "found": 0, "new": 0, "updated": 0, "errors": str(last_err)}

    print(f"  [{source}] Found {len(listings)} listings on page")

    known              = _get_known_ids(source)
    if known_urls is not None and known_vins is not None:
        _known_urls, _known_vins = known_urls, known_vins
    else:
        _known_urls, _known_vins = _get_known_urls_and_vins()
    known_fingerprints = _get_known_fingerprints(source)
    new_count = 0
    updated_count = 0
    skipped_count = 0
    enrichment_failed = 0

    # Pass 1: identify candidates (passes validation + dedup checks)
    candidates = []
    existing   = []
    for listing in listings:
        if not _is_valid(listing, min_year, makes, allowed_models, max_year, max_price, max_mileage, radius):
            skipped_count += 1
            continue
        vin = listing.get("vin")
        if vin and vin in _known_vins:
            skipped_count += 1
            continue
        fp = (listing.get("year"), listing.get("mileage"), listing.get("price"))
        if all(v is not None for v in fp) and fp in known_fingerprints:
            skipped_count += 1
            continue
        url = listing.get("listing_url", "")
        if url and url in _known_urls:
            skipped_count += 1
            continue
        ext_id = str(listing["external_id"])
        if ext_id not in known:
            candidates.append(listing)
        else:
            existing.append((source, ext_id, listing["price"], known[ext_id]))

    # Pass 2: enrich new candidates with detail-page history data.
    # Only CarGurus and Cars.com require enrichment — their listing pages don't
    # surface accident/title data, so the detail page is the only source of truth.
    # CarMax, Carvana, and dealer sources already set these fields from their
    # own scrapers; fetching detail pages for them adds latency with no benefit.
    _ENRICHMENT_REQUIRED = {"cargurus", "carscom"}
    if candidates and source in _ENRICHMENT_REQUIRED:
        for listing in candidates:
            listing["accident_count"] = None
            listing["title_status"] = None
        print(f"  [{source}] Fetching history for {len(candidates)} new listing(s)...")
        candidates = enrich_listings_from_detail(candidates)

    # Pass 3: save/update
    for listing in candidates:
        try:
            if source in _ENRICHMENT_REQUIRED:
                # Reject if enrichment never ran or timed out
                if listing.get("accident_count") is None:
                    enrichment_failed += 1
                    skipped_count += 1
                    continue
                # Re-check only the two fields enrichment updated — all other
                # criteria (price, year, mileage, make, model, trim, distance)
                # already passed in Pass 1 and didn't change.
                title = (listing.get("title_status") or "unknown").lower()
                if title in ("salvage", "rebuilt", "flood", "lemon", "fleet"):
                    skipped_count += 1
                    continue
                if (listing.get("accident_count") or 0) > 0:
                    skipped_count += 1
                    continue
            vin = listing.get("vin")
            url = listing.get("listing_url", "")
            _save_new(listing)
            if vin: _known_vins.add(vin)
            _known_urls.add(url)
            new_count += 1
        except Exception as e:
            print(f"  [{source}] listing error (skipping): {e}")
            skipped_count += 1

    for _, ext_id, new_price, old_price in existing:
        try:
            price_changed = _touch_existing(source, ext_id, new_price, old_price)
            if price_changed:
                updated_count += 1
        except Exception as e:
            print(f"  [{source}] touch error (skipping): {e}")

    enrich_note = f", {enrichment_failed} enrichment-failed" if enrichment_failed else ""
    print(f"  [{source}] {new_count} new, {updated_count} price changes, {skipped_count} filtered{enrich_note}")
    return {
        "source": source,
        "skipped": False,
        "found": len(listings),
        "new": new_count,
        "updated": updated_count,
        "errors": None,
    }


def run(run_id: str = "", force: bool = False) -> dict:
    settings = fetch_one("SELECT * FROM user_settings LIMIT 1") or {}
    zip_code       = settings.get("zip_code", "30328")
    radius         = settings.get("radius_miles", 500)
    makes          = get_allowed_makes()
    allowed_models = get_allowed_models()
    vehicles       = get_vehicles()          # [(make, model)] for targeted model filtering
    max_price      = settings.get("max_price", 35000)
    max_mileage    = settings.get("max_mileage", 30000)
    min_year       = settings.get("min_year", 2025)
    max_year       = settings.get("max_year", 2026)
    # Lower the scrape floor if any model has an earlier per-model min_year.
    # _is_valid() still enforces the per-model constraints after scraping.
    per_model_mins = [get_model_min_year(mk, mo) for mk, mo in vehicles]
    scrape_min_year = min([min_year] + [y for y in per_model_mins if y is not None])
    if scrape_min_year < min_year:
        min_year = scrape_min_year

    global _FORCE_MODE
    _FORCE_MODE = force

    try:
        source_results: dict[str, int] = {}
        source_errors: dict[str, str] = {}

        # Pre-fetch cross-source dedup sets once — shared across all scrapers and
        # the dealer section so we avoid O(sources) redundant DB round-trips and
        # get consistent in-run dedup (a VIN saved by CarGurus blocks Cars.com, etc.)
        shared_known_urls, shared_known_vins = _get_known_urls_and_vins()

        for source, fn in SCRAPERS.items():
            stats = process_source(
                source, fn, zip_code, makes, max_price, max_mileage, min_year,
                allowed_models, max_year, vehicles, radius,
                known_urls=shared_known_urls, known_vins=shared_known_vins,
            )
            source_results[source] = stats["found"]
            if stats.get("errors"):
                source_errors[source] = stats["errors"]

        # Dealer websites — reuse the same shared sets (already updated by SCRAPERS loop)
        print("  [dealer] Scraping authorized dealer sites...")
        known_dealer = _get_known_ids("dealer")
        known_fingerprints = _get_known_fingerprints("dealer")
        dealer_total = dealer_new = dealer_skipped = 0
        try:
            for listing in scrape_all_dealers(makes, min_year, max_year):
                dealer_total += 1
                if not _is_valid(listing, min_year, makes, allowed_models, max_year, max_price, max_mileage, radius):
                    dealer_skipped += 1
                    continue
                vin = listing.get("vin")
                if vin and vin in shared_known_vins:
                    dealer_skipped += 1
                    continue
                url = listing.get("listing_url", "")
                if url and url in shared_known_urls:
                    dealer_skipped += 1
                    continue
                fp = (listing.get("year"), listing.get("mileage"), listing.get("price"))
                if all(v is not None for v in fp) and fp in known_fingerprints:
                    dealer_skipped += 1
                    continue
                ext_id = str(listing["external_id"])
                if ext_id not in known_dealer:
                    _save_new(listing)
                    known_dealer[ext_id] = listing["price"]
                    if vin: shared_known_vins.add(vin)
                    shared_known_urls.add(url)
                    if all(v is not None for v in fp):
                        known_fingerprints.add(fp)
                    dealer_new += 1
                else:
                    _touch_existing("dealer", ext_id, listing["price"], known_dealer[ext_id])
            source_results[DEALER_SOURCE] = dealer_total
            print(f"  [dealer] {dealer_new} new, {dealer_skipped} filtered from {dealer_total} raw")
        except Exception as e:
            source_errors[DEALER_SOURCE] = str(e)
            print(f"  [dealer] ERROR: {e}")

        return {"source_results": source_results, "source_errors": source_errors}
    finally:
        _FORCE_MODE = False  # always reset — guards against exception in SCRAPERS loop


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    result = run("manual", force=force)
    print(json.dumps(result, indent=2, default=str))
