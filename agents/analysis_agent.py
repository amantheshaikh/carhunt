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
from tools.price_model import predict_fair_price, explain_listing
from tools.scoring import score_listing


def run() -> dict:
    rows = fetch_all(
        """SELECT l.* FROM listings l
           WHERE l.status IN ('new', 'active', 'contacted')
           ORDER BY l.first_seen_at DESC"""
    )

    score_rows: list[tuple] = []
    to_activate: list[str] = []

    for row in rows:
        listing = dict(row)
        lid = str(listing["id"])

        ml_price = predict_fair_price(listing)
        score = score_listing(listing["price"], ml_price)

        if score is None:
            # No ML model yet — activate so it shows as Unscored
            to_activate.append(lid)
            continue

        explanation = explain_listing(listing)
        score_rows.append((lid, score, ml_price, json.dumps(explanation) if explanation else None))
        to_activate.append(lid)

    # Batch upsert scores
    execute_values(
        """INSERT INTO listing_scores
               (listing_id, value_score, market_median_price, market_price_source, shap_values, scored_at)
           VALUES %s
           ON CONFLICT (listing_id) DO UPDATE SET
               value_score = EXCLUDED.value_score,
               market_median_price = EXCLUDED.market_median_price,
               market_price_source = EXCLUDED.market_price_source,
               shap_values = EXCLUDED.shap_values,
               scored_at = now()""",
        [(lid, score, mp, shap) for lid, score, mp, shap in score_rows],
        template="(%s::uuid, %s, %s, 'ml_model', %s, now())",
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
