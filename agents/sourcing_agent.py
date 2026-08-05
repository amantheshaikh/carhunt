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
import sys
import time
import traceback
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env.local"))

sys.stdout.reconfigure(line_buffering=True)

from psycopg2.errors import UniqueViolation
from tools.db import fetch_one, fetch_all, execute, get_conn
from tools.scrapers import (
    scrape_cargurus, scrape_carmax, scrape_carvana,
    scrape_carscom, enrich_listings_from_detail,
)
from tools.dealer_scraper import scrape_all_dealers
from tools.base_trims import get_allowed_makes, get_allowed_models, get_vehicles, is_trim_allowed, get_model_min_year, get_model_max_year, get_trim_min_year, get_trim_max_year, trim_belongs_to_different_model, canonical_trim, _norm, normalize_listing_model

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
_refresh_log_cache: dict | None = None  # cached per run() call to avoid N identical DB queries


def _should_skip_source(source: str) -> tuple[bool, str]:
    """Return (skip, reason). Skip if recently scraped with active results."""
    if _FORCE_MODE:
        return False, "force mode"
    global _refresh_log_cache
    if _refresh_log_cache is None:
        _refresh_log_cache = fetch_one(
            """SELECT completed_at, source_results, source_errors
               FROM refresh_log
               WHERE completed_at IS NOT NULL
                 AND source_results IS NOT NULL
               ORDER BY completed_at DESC
               LIMIT 1"""
        )
    row = _refresh_log_cache
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


def _get_known_fingerprints(exclude_source: str) -> tuple[set[tuple], set[tuple]]:
    """Return (price_fps, dealer_fps) for non-dismissed listings from OTHER sources.

    price_fps  — exact (year, mileage, price) match; catches duplicates when price is identical.
    dealer_fps — (dealer_name, year, mileage) match; catches cross-source duplicates when the
                 same car is listed at slightly different prices on CarGurus vs the dealer site.
    """
    rows = fetch_all(
        "SELECT make, model, year, mileage, price, dealer_name FROM listings "
        "WHERE source != %s AND status NOT IN ('dismissed') "
        "AND year IS NOT NULL AND mileage IS NOT NULL AND price IS NOT NULL",
        (exclude_source,)
    )
    price_fps  = {(r["year"], r["mileage"], r["price"]) for r in rows}
    dealer_fps = {
        (r["dealer_name"], r["make"], r["model"], r["year"], r["mileage"])
        for r in rows
        if r["dealer_name"] and r["make"] and r["model"] and r["mileage"] is not None
    }
    return price_fps, dealer_fps


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
        # Reject rental/fleet usage: >14k miles/year indicates commercial use
        if mileage is not None and year is not None:
            years_old = max(1, datetime.now().year - int(year) + 1)
            if mileage / years_old > 14_000:
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
    mk, mo = normalize_listing_model(listing["make"], listing["model"], listing.get("trim"))
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
               ON CONFLICT (source, external_id) DO UPDATE SET
                   trim = EXCLUDED.trim,
                   dealer_name = EXCLUDED.dealer_name,
                   insights = COALESCE(EXCLUDED.insights, listings.insights)""",
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
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE listings SET last_seen_at = now(), price = %s WHERE source = %s AND external_id = %s",
                    (new_price, source, external_id),
                )
                # Re-activate listings the user marked expired if they've come back on market.
                # Don't touch 'purchased' or 'dismissed' — those are intentional user states.
                cur.execute(
                    "UPDATE listings SET status = 'active' WHERE source = %s AND external_id = %s AND status = 'expired'",
                    (source, external_id),
                )
                if price_changed:
                    cur.execute(
                        "DELETE FROM listing_scores WHERE listing_id = "
                        "(SELECT id FROM listings WHERE source=%s AND external_id=%s)",
                        (source, external_id),
                    )
    finally:
        conn.close()
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
        print(f"  [{source}] SKIP — {reason}", flush=True)
        return {"source": source, "skipped": True, "reason": reason, "found": 0, "new": 0, "updated": 0, "errors": None}

    print(f"  [{source}] Scraping (radius={radius} mi)...", flush=True)
    kwargs: dict = {}
    if max_year:
        kwargs["max_year"] = max_year
    if source in ("cargurus", "carmax", "carscom"):
        kwargs["radius"] = radius
    if source == "carscom":
        kwargs["clean_history"] = True
    if vehicles:
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
            print(f"  [{source}] attempt {attempt} failed: {e}  (retry in {wait}s)", flush=True)
            if attempt < 3:
                time.sleep(wait)
    else:
        print(f"  [{source}] ERROR after 3 attempts — {last_err}", flush=True)
        traceback.print_exc()
        return {"source": source, "skipped": False, "found": 0, "new": 0, "updated": 0, "errors": str(last_err)}

    print(f"  [{source}] Found {len(listings)} listings on page", flush=True)

    known              = _get_known_ids(source)
    if known_urls is not None and known_vins is not None:
        _known_urls, _known_vins = known_urls, known_vins
    else:
        _known_urls, _known_vins = _get_known_urls_and_vins()
    known_price_fps, known_dealer_fps = _get_known_fingerprints(source)
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
        # Check same-source ext_id FIRST so price updates always reach _touch_existing,
        # even when the listing URL is already in _known_urls (which would otherwise
        # short-circuit the ext_id check and silently drop price drops).
        ext_id = str(listing["external_id"])
        if ext_id in known:
            existing.append((source, ext_id, listing["price"], known[ext_id]))
            continue
        # New ext_id — apply cross-source dedup before treating as a candidate.
        vin = listing.get("vin")
        if vin and vin in _known_vins:
            skipped_count += 1
            continue
        fp = (listing.get("year"), listing.get("mileage"), listing.get("price"))
        if all(v is not None for v in fp) and fp in known_price_fps:
            skipped_count += 1
            continue
        dfp = (listing.get("dealer_name"), listing.get("make"), listing.get("model"), listing.get("year"), listing.get("mileage"))
        if all(v is not None for v in dfp) and dfp in known_dealer_fps:
            skipped_count += 1
            continue
        url = listing.get("listing_url", "")
        if url and url in _known_urls:
            skipped_count += 1
            continue
        candidates.append(listing)

    # Pass 2: enrich new candidates with detail-page data.
    # Required sources (cargurus, carscom): accident/title come only from the detail page,
    # so we reset them here and reject any listing that times out or fails to enrich.
    # Optional sources (dealer): scrapers already set accident=0/title=clean; enrichment
    # is best-effort to extract LLM insights (CPO, packages, price_drop, owner_notes).
    # CarMax and Carvana carry full history data from their own scrapers — no enrichment needed.
    _ENRICHMENT_REQUIRED = {"cargurus", "carscom"}
    _ENRICHMENT_OPTIONAL = {"dealer"}
    if candidates and source in (_ENRICHMENT_REQUIRED | _ENRICHMENT_OPTIONAL):
        if source in _ENRICHMENT_REQUIRED:
            for listing in candidates:
                listing["accident_count"] = None
                listing["title_status"] = None
        label = "history" if source in _ENRICHMENT_REQUIRED else "insights"
        print(f"  [{source}] Fetching {label} for {len(candidates)} new listing(s)...", flush=True)
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
            cur_fp = (listing.get("year"), listing.get("mileage"), listing.get("price"))
            if all(v is not None for v in cur_fp):
                known_price_fps.add(cur_fp)
            new_count += 1
        except Exception as e:
            print(f"  [{source}] listing error (skipping): {e}", flush=True)
            skipped_count += 1

    for _, ext_id, new_price, old_price in existing:
        try:
            price_changed = _touch_existing(source, ext_id, new_price, old_price)
            if price_changed:
                updated_count += 1
        except Exception as e:
            print(f"  [{source}] touch error (skipping): {e}", flush=True)

    enrich_note = f", {enrichment_failed} enrichment-failed" if enrichment_failed else ""
    print(f"  [{source}] {new_count} new, {updated_count} price changes, {skipped_count} filtered{enrich_note}", flush=True)
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
    # Set max_mileage to 999999 to disable hard mileage ceiling (rely on mileage-per-year/rental check)
    max_mileage    = 999999
    min_year       = settings.get("min_year", 2025)
    max_year       = settings.get("max_year", 2026)
    # Lower the scrape floor if any model has an earlier per-model min_year.
    # _is_valid() still enforces the per-model constraints after scraping.
    per_model_mins = [get_model_min_year(mk, mo) for mk, mo in vehicles]
    scrape_min_year = min([min_year] + [y for y in per_model_mins if y is not None])
    if scrape_min_year < min_year:
        min_year = scrape_min_year

    global _FORCE_MODE, _refresh_log_cache
    _FORCE_MODE = force
    _refresh_log_cache = None  # reset per-run cache

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
        print("  [dealer] Scraping authorized dealer sites...", flush=True)
        known_dealer = _get_known_ids("dealer")
        known_dealer_price_fps, known_dealer_dfps = _get_known_fingerprints("dealer")
        dealer_total = dealer_new = dealer_skipped = 0
        new_dealer_listings = []
        try:
            for listing in scrape_all_dealers(makes, min_year, max_year, force=_FORCE_MODE):
                dealer_total += 1
                if not _is_valid(listing, min_year, makes, allowed_models, max_year, max_price, max_mileage, radius):
                    dealer_skipped += 1
                    continue
                # Check same-source ext_id first so price drops always reach _touch_existing.
                ext_id = str(listing["external_id"])
                if ext_id in known_dealer:
                    _touch_existing("dealer", ext_id, listing["price"], known_dealer[ext_id])
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
                if all(v is not None for v in fp) and fp in known_dealer_price_fps:
                    dealer_skipped += 1
                    continue
                dfp = (listing.get("dealer_name"), listing.get("make"), listing.get("model"), listing.get("year"), listing.get("mileage"))
                if all(v is not None for v in dfp) and dfp in known_dealer_dfps:
                    dealer_skipped += 1
                    continue
                new_dealer_listings.append(listing)

            if new_dealer_listings:
                print(f"  [dealer] Fetching VDP insights for {len(new_dealer_listings)} new listing(s)...", flush=True)
                new_dealer_listings = enrich_listings_from_detail(new_dealer_listings)
                for listing in new_dealer_listings:
                    vin = listing.get("vin")
                    url = listing.get("listing_url", "")
                    fp = (listing.get("year"), listing.get("mileage"), listing.get("price"))
                    dfp = (listing.get("dealer_name"), listing.get("make"), listing.get("model"), listing.get("year"), listing.get("mileage"))
                    ext_id = str(listing["external_id"])
                    
                    _save_new(listing)
                    known_dealer[ext_id] = listing["price"]
                    if vin: shared_known_vins.add(vin)
                    if url: shared_known_urls.add(url)
                    if all(v is not None for v in fp):
                        known_dealer_price_fps.add(fp)
                    if all(v is not None for v in dfp):
                        known_dealer_dfps.add(dfp)
                    dealer_new += 1
            source_results[DEALER_SOURCE] = dealer_total
            print(f"  [dealer] {dealer_new} new, {dealer_skipped} filtered from {dealer_total} raw", flush=True)
        except Exception as e:
            source_errors[DEALER_SOURCE] = str(e)
            print(f"  [dealer] ERROR: {e}", flush=True)

        return {"source_results": source_results, "source_errors": source_errors}
    finally:
        _FORCE_MODE = False  # always reset — guards against exception in SCRAPERS loop


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    result = run("manual", force=force)
    print(json.dumps(result, indent=2, default=str))
