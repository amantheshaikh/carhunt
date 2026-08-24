#!/usr/bin/env python3
"""
backfill_training.py — targeted fix of price_training_data for one model.

Two modes:

  rederive (default)
    Re-applies drivetrain/powertrain overrides and canonical trim in-place
    using stored columns. No network. Fast (~seconds).
    Fixes: model-year overrides (_DRIVETRAIN_DEFAULTS), trim-level overrides
           (_TRIM_DRIVETRAIN), canonical trim name changes.
    Cannot fix: B4/B5 engine variants — that info is stripped by canonicalization
    and the original raw trim is not stored. Use --mode rescrape for that.

  rescrape
    Deletes all rows for the model and re-collects from CarGurus and Cars.com
    using current detection logic. Requires Playwright.
    Takes 5–15 minutes per model. Fixes everything, including B4/B5 drivetrain.

Usage:
  python3 backfill_training.py --model tiguan
  python3 backfill_training.py --model xc40 --mode rescrape
  python3 backfill_training.py --model "tucson hybrid" --dry-run
  python3 backfill_training.py --model xc40 --mode rescrape --retrain
  python3 backfill_training.py --model tiguan --retrain --yes
"""
import argparse
import os
import sys

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env.local"))

sys.stdout.reconfigure(line_buffering=True)

from tools.db import execute, fetch_all, fetch_one, refresh_self_computed_medians, execute_values
from tools.base_trims import _norm, canonical_trim, get_vehicles
from tools.scoring import detect_drivetrain, detect_powertrain
from tools.price_model import (
    _drivetrain_for, _pkl_path,
    _fit_stacked, load_training_data_for, _ALL_MODELS,
    _is_cpo,
)
from tools.job_tracker import start_job, update_job, complete_job

from tools.log_tee import enable_log_tee as _enable_log_tee


# ── helpers ──────────────────────────────────────────────────────────────────

def _resolve_model(name: str) -> tuple[str, str]:
    """Return (make, canonical_model_name) for a case-insensitive model lookup."""
    name_norm = _norm(name)
    for make, model in get_vehicles():
        if _norm(model) == name_norm:
            return make, model
    available = ", ".join(f'"{m}"' for _, m in get_vehicles())
    sys.exit(f'Model "{name}" not found in cars.json. Available: {available}')


def _print_stats(model_name: str, target_year: int | None = None) -> int:
    year_clause = "AND year = %s" if target_year else ""
    year_param = (target_year,) if target_year else ()
    rows = fetch_all(f"""
        SELECT drivetrain, powertrain, COUNT(*) AS n
        FROM price_training_data
        WHERE lower(model) = %s {year_clause}
        GROUP BY drivetrain, powertrain
        ORDER BY drivetrain, powertrain
    """, (model_name.lower(), *year_param))
    total = sum(r["n"] for r in rows)
    year_str = f" for year {target_year}" if target_year else ""
    print(f"\n  Current rows in DB for '{model_name}'{year_str}: {total}")
    if rows:
        print("  Breakdown (drivetrain × powertrain):")
        for r in rows:
            print(f"    {r['drivetrain'] or 'NULL':10s} × {r['powertrain'] or 'NULL':8s} : {r['n']}")
    return total


# ── rederive mode ─────────────────────────────────────────────────────────────

def rederive(make: str, model_name: str, dry_run: bool, job_id: int | None, target_year: int | None = None) -> None:
    year_clause = "AND year = %s" if target_year else ""
    year_param = (target_year,) if target_year else ()
    print(f"\n[rederive] Re-applying detection logic for '{model_name}' (year: {target_year or 'all'}) from stored columns...")
    if job_id:
        update_job(job_id, stage_current=1, stage_label=f"Re-deriving {model_name}")

    rows = fetch_all(f"""
        SELECT ptd.id, ptd.make, ptd.model, ptd.year, ptd.trim, ptd.drivetrain, ptd.powertrain, ptd.condition, ptd.cpo, ptd.listing_url,
               (l.insights->>'cpo')::boolean AS listing_cpo, l.raw AS listing_raw
        FROM price_training_data ptd
        LEFT JOIN listings l ON ptd.listing_url = l.listing_url
        WHERE lower(ptd.model) = %s {year_clause}
    """, (model_name.lower(), *year_param))

    updates: list[tuple] = []
    for row in rows:
        row_dict = {
            "make": row["make"],
            "model": row["model"],
            "year": row["year"],
            "trim": row["trim"],
            "condition": row["condition"],
            "cpo": row["listing_cpo"] if row["listing_cpo"] is not None else row["cpo"],
            "raw": row["listing_raw"] if row["listing_raw"] else {}
        }
        
        new_cpo = _is_cpo(row_dict)
        
        fake = {"make": row["make"], "model": row["model"], "year": row["year"]}
        model_lower = row["model"].lower()
        new_dt   = _drivetrain_for(fake, row["trim"], model_lower, raw=None)
        new_pt   = detect_powertrain(row["trim"], model_lower, raw_text=None)
        new_trim = canonical_trim(row["make"], row["model"], row["trim"])

        # Never downgrade a specific value to "unknown" — "unknown" means
        # "can't determine from stored columns", not that the stored value is wrong.
        if new_dt == "unknown":
            new_dt = row["drivetrain"]
        if new_pt == "unknown":
            new_pt = row["powertrain"]

        if (new_dt != row["drivetrain"] or 
            new_pt != row["powertrain"] or 
            new_trim != row["trim"] or 
            new_cpo != row["cpo"]):
            
            updates.append((new_dt, new_pt, new_trim, new_cpo, row["id"],
                            row["drivetrain"], row["powertrain"], row["trim"], row["cpo"]))

    print(f"  {len(rows)} total rows, {len(updates)} need updating")
    if not updates:
        print("  Nothing to change.")
        return

    for new_dt, new_pt, new_trim, new_cpo, row_id, old_dt, old_pt, old_trim, old_cpo in updates[:5]:
        changes = []
        if new_dt != old_dt:     changes.append(f"drivetrain {old_dt!r} → {new_dt!r}")
        if new_pt != old_pt:     changes.append(f"powertrain {old_pt!r} → {new_pt!r}")
        if new_trim != old_trim: changes.append(f"trim {old_trim!r} → {new_trim!r}")
        if new_cpo != old_cpo:   changes.append(f"cpo {old_cpo!r} → {new_cpo!r}")
        print(f"    {row_id}:  {', '.join(changes)}")
    if len(updates) > 5:
        print(f"    ... and {len(updates) - 5} more")

    if dry_run:
        print("\n  [dry-run] No changes committed.")
        return

    execute_values(
        """UPDATE price_training_data AS ptd
           SET drivetrain = data.dt, powertrain = data.pt, trim = data.tr, cpo = data.cpo
           FROM (VALUES %s) AS data(dt, pt, tr, cpo, id)
           WHERE ptd.id = data.id::uuid""",
        [(new_dt, new_pt, new_trim, new_cpo, row_id) for new_dt, new_pt, new_trim, new_cpo, row_id, *_ in updates],
        template="(%s, %s, %s, %s, %s)",
    )
    print(f"\n  Updated {len(updates)} rows.")


# ── rescrape mode ─────────────────────────────────────────────────────────────

def rescrape(make: str, model_name: str, dry_run: bool, yes: bool, job_id: int | None, target_year: int | None = None) -> None:
    _print_stats(model_name, target_year=target_year)

    print(f"\n  Re-collecting training data for '{model_name}'...")

    from training_collector import STAGES_BROAD, run_stage, _norm as tc_norm
    from tools.price_model import _PLATFORM_MIN_YEAR
    from tools.base_trims import get_model_min_year, get_model_max_year
    allowed_norm = {tc_norm(model_name)}
    makes        = [make]
    vehicles     = [(make, model_name)]

    # Clamp each stage's min_year to the model's effective minimum so the scraper
    # doesn't fetch years we'll discard. Skip stages that start above model_max entirely.
    model_min = max(
        _PLATFORM_MIN_YEAR.get(_norm(model_name), 0),
        get_model_min_year(make, model_name) or 0,
    )
    model_max = get_model_max_year(make, model_name)

    if target_year:
        if target_year < model_min or (model_max and target_year > model_max):
            sys.exit(f"Requested year {target_year} is outside target range ({model_min}-{model_max or '∞'}) for {model_name}")

    applicable: list[tuple] = []
    for desc, mn, mmi, mp in STAGES_BROAD:
        if target_year:
            if mn > target_year:
                continue
            if model_min and mn < model_min:
                continue
            effective_mn = target_year
            effective_mx = target_year
        else:
            if model_max and mn > model_max:
                continue
            if model_min and mn < model_min:
                continue
            effective_mn = max(mn, model_min) if model_min else mn
            effective_mx = model_max

        applicable.append((desc, effective_mn, effective_mx, mmi, mp))

    skipped = len(STAGES_BROAD) - len(applicable)
    if target_year:
        print(f"  Year filter: exactly {target_year} ({skipped} stages skipped)")
    elif skipped or model_min:
        print(f"  Year filter: {model_min or '?'}–{model_max or '∞'}  "
              f"({skipped} stages skipped, min_year clamped where needed)")

    grand_total = 0
    cg_scraped: set[tuple] = set()  # dedup CarGurus across stages (same year+model = same URLs)
    for i, (desc, min_year, max_year, max_mileage, max_price) in enumerate(applicable, 1):
        price_label = "no cap" if max_price >= 999_999 else f"${max_price:,}"
        print(f"  [{i:02d}/{len(applicable)}] {desc}  (yr={min_year}, mi<={max_mileage:,}, price<={price_label})")
        if job_id:
            update_job(job_id, stage_current=i, stage_label=desc, rows_added=grand_total)
        if not dry_run:
            n = run_stage(desc, min_year, max_mileage, max_price, makes, allowed_norm, vehicles,
                          cg_scraped=cg_scraped, max_year=max_year)
            grand_total += n
            print(f"    → {n} new rows")

    if not dry_run:
        print(f"\n  Total new rows inserted: {grand_total}")
        if job_id:
            update_job(job_id, rows_added=grand_total)
        _print_stats(model_name, target_year=target_year)


# ── retrain one model ─────────────────────────────────────────────────────────

def retrain_one(model_name: str, job_id: int | None, stage_offset: int = 0) -> None:
    model_lower = model_name.lower()
    if model_lower not in _ALL_MODELS:
        print(f"\n  [retrain] '{model_lower}' not in _ALL_MODELS — skipping.")
        return

    if job_id:
        update_job(job_id, stage_current=stage_offset + 1, stage_label=f"Retraining {model_name} (GB)")

    print(f"\n[retrain] Retraining GB price model for '{model_lower}'...")
    X, y = load_training_data_for(model_lower)
    _fit_stacked(model_lower, X, y, model_lower, _pkl_path(model_lower), save=True)

    if job_id:
        update_job(job_id, stage_current=stage_offset + 2, stage_label="Refreshing medians")
    print(f"\n  Refreshing market price medians...")
    cached = refresh_self_computed_medians()
    print(f"  Updated {cached} cache entries.")
    print(f"\n  Done. Run 'Refresh Now' in the dashboard to rescore active listings.")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model",   required=True,
                        help='Model name, e.g. "tiguan" or "xc40"')
    parser.add_argument("--year",    type=int,
                        help="Limit backfill/rederive/rescrape to a specific model year (e.g. 2023)")
    parser.add_argument("--mode",    choices=["rederive", "rescrape"], default="rederive",
                        help="rederive: SQL-only in-place fix (default); rescrape: delete + re-collect")
    parser.add_argument("--retrain", action="store_true",
                        help="Retrain price/KNN/additive models after the backfill")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen without committing any changes")
    parser.add_argument("--yes",     action="store_true",
                        help="Skip confirmation prompts (for --mode rescrape)")
    args = parser.parse_args()

    make, model_name = _resolve_model(args.model)

    if not args.dry_run:
        _enable_log_tee()

    print(f"\n{'='*62}")
    print(f"  backfill_training — {args.mode.upper()} mode")
    print(f"  Model : {model_name}  ({make})")
    if args.dry_run:
        print(f"  Mode  : DRY RUN — no changes will be committed")
    print(f"{'='*62}")

    _print_stats(model_name, target_year=args.year)

    # Compute total stages for job progress bar (rescrape uses filtered stage count)
    from training_collector import STAGES_BROAD
    from tools.price_model import _PLATFORM_MIN_YEAR
    from tools.base_trims import get_model_min_year, get_model_max_year
    if args.mode == "rescrape":
        _eff_min = max(_PLATFORM_MIN_YEAR.get(_norm(model_name), 0), get_model_min_year(make, model_name) or 0)
        _eff_max = get_model_max_year(make, model_name)
        if args.year:
            if args.year < _eff_min or (_eff_max and args.year > _eff_max):
                scrape_stages = 0
            else:
                scrape_stages = sum(1 for _, mn, _, _ in STAGES_BROAD if not (mn > args.year))
        else:
            scrape_stages = sum(1 for _, mn, _, _ in STAGES_BROAD if not (_eff_max and mn > _eff_max))
    else:
        scrape_stages = 1
    retrain_stages = 2 if args.retrain else 0
    stage_total = scrape_stages + retrain_stages

    job_id: int | None = None
    if not args.dry_run:
        job_id = start_job("backfill", stage_total=stage_total)

    try:
        if args.mode == "rederive":
            rederive(make, model_name, dry_run=args.dry_run, job_id=job_id, target_year=args.year)
        else:
            rescrape(make, model_name, dry_run=args.dry_run, yes=args.yes, job_id=job_id, target_year=args.year)

        if args.retrain:
            if args.dry_run:
                print("\n  [dry-run] Would retrain models.")
            else:
                retrain_one(model_name, job_id=job_id, stage_offset=scrape_stages)
        else:
            print(f"\n  Tip: add --retrain to rebuild the ML model with updated data.")

        if job_id:
            complete_job(job_id)
    except Exception:
        if job_id:
            complete_job(job_id, status="error")
        raise


if __name__ == "__main__":
    main()
