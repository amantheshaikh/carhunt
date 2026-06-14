"""
Analysis Agent — scores every unscored listing using the ML price model.
No LLM. Score = sigmoid(price deviation from ML fair value).
All features (year, mileage, trim, drivetrain, powertrain) are already
encoded in the ML prediction, so the score is purely price vs fair value.
"""
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env.local"))

import json
from tools.db import fetch_all, execute, execute_values
from tools.price_model import predict_fair_price, explain_listing, resolve_drivetrain
from tools.scoring import score_listing


def run() -> dict:
    rows = fetch_all(
        """SELECT l.* FROM listings l
           WHERE l.status IN ('new', 'active')
           ORDER BY l.first_seen_at DESC"""
    )

    score_rows: list[tuple] = []
    to_activate: list[str] = []
    drivetrain_updates: list[tuple] = []  # (resolved_drivetrain, listing_id)

    for row in rows:
        listing = dict(row)
        lid = str(listing["id"])

        # Apply drivetrain business rules/overrides to enforce correct values (e.g. xDrive, sDrive, Volvo AWD rules)
        resolved = resolve_drivetrain(listing)
        if resolved and resolved != "unknown" and resolved != listing.get("drivetrain"):
            drivetrain_updates.append((resolved, lid))
            listing["drivetrain"] = resolved  # use resolved value for scoring features too

        gb_price, residual_std = predict_fair_price(listing)
        score = score_listing(listing["price"], gb_price, residual_std or 0.0)

        if score is None:
            # No ML model yet — activate so it shows as Unscored
            to_activate.append(lid)
            continue

        explanation = explain_listing(listing)
        score_rows.append((lid, score, gb_price, json.dumps(explanation) if explanation else None))
        to_activate.append(lid)

    # Batch upsert scores
    execute_values(
        """INSERT INTO listing_scores
               (listing_id, value_score, fair_price, market_price_source, shap_values, scored_at)
           VALUES %s
           ON CONFLICT (listing_id) DO UPDATE SET
               value_score = EXCLUDED.value_score,
               fair_price = EXCLUDED.fair_price,
               market_price_source = EXCLUDED.market_price_source,
               shap_values = EXCLUDED.shap_values,
               scored_at = now()""",
        [(lid, score, mp, shap) for lid, score, mp, shap in score_rows],
        template="(%s::uuid, %s, %s, 'ml_model', %s, now())",
    )

    # Backfill resolved drivetrains for listings that had none
    if drivetrain_updates:
        execute_values(
            "UPDATE listings SET drivetrain = data.dt FROM (VALUES %s) AS data(dt, id) WHERE listings.id = data.id::uuid",
            drivetrain_updates,
            template="(%s, %s)",
        )

    # Activate new listings (no auto-dismiss — user controls this manually)
    if to_activate:
        execute(
            "UPDATE listings SET status = 'active' WHERE id = ANY(%s::uuid[]) AND status = 'new'",
            (to_activate,),
        )

    return {"scored": len(score_rows)}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
