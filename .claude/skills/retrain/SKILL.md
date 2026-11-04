---
name: retrain
description: >
  Retrains the CarHunt ML fair-price models with broad, fresh market data.
  Use when the user says: "retrain", "retrain the model", "/retrain", "update the price model",
  "collect training data", "refresh the ML model", or any variation of wanting to rebuild
  the price estimators with new market data.
---

Retrain the CarHunt ML fair-price models. Run the following two steps in sequence.

## Step 1 — Broad market data collection

Run the training collector in `--broad` mode. This scrapes **CarGurus, Cars.com, Carvana, and CarMax** nationwide (radius=2500mi, no geographic constraint) across 16 stages covering model years **2023–2026 with no price ceiling** on high-trim stages. This fills in the full depreciation curve that the ML model needs.

```bash
cd agents && .venv/bin/python3 training_collector.py --broad
```

This takes 15–30 minutes. Stream the output so the user sees progress per stage.

## Step 2 — Retrain per-model estimators

After collection completes, retrain all six per-model HistGradientBoostingRegressor models:

```bash
cd agents && .venv/bin/python3 -c "
from dotenv import load_dotenv
load_dotenv('../.env.local')
from tools.price_model import train
train()
"
```

## Step 3 — Report results

After retraining, show a summary table:

| Model | Training rows | MAE | Status |
|-------|--------------|-----|--------|
| rav4 | N | $X | fitted |
| tucson hybrid | N | $X | fitted |
| ... | | | |
| _global | N | $X | fallback |

Note any models with < 30 rows that fell back to the global estimator — those need more scrape cycles.

## Step 4 — Rescore active listings

Tell the user to click **Refresh Now** in the dashboard, or run the orchestrator directly to rescore all active listings with the updated model:

```bash
cd agents && .venv/bin/python3 orchestrator.py
```
