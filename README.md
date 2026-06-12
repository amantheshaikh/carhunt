# CarHunt

Personal used-car deal tracker for the Atlanta metro area. Scrapes CarGurus, Cars.com, CarMax, Carvana, and local dealer websites for target vehicle configurations, scores each listing with an ML fair-price model, and surfaces the best deals in a Next.js dashboard.

---

## Prerequisites

- **Node.js** v18+ and npm
- **Python** 3.11+
- **Docker Desktop** (for PostgreSQL)

---

## First-time setup

### 1. Start the database

```bash
docker compose up -d
```

This starts a PostgreSQL container on port 5432 and runs `db/schema.sql` automatically on first launch.

### 2. Set your API keys

Copy `.env.example` to `.env.local` and fill in your keys:

```bash
cp .env.example .env.local
```

Edit `.env.local`:

```
DATABASE_URL=postgresql://carhunt:carhunt@localhost:5432/carhunt
OPENROUTER_API_KEY=your_key_here
AGENTS_DIR=/path/to/carhunt/agents
AGENTS_PYTHON=/path/to/carhunt/agents/.venv/bin/python3
```

Get an OpenRouter key at [openrouter.ai](https://openrouter.ai). The app uses `deepseek/deepseek-v4-flash` for LLM calls (insight extraction, trim normalization, refresh summaries).

### 3. Install Node dependencies

```bash
npm install
```

### 4. Set up Python environment

```bash
cd agents
python3 -m venv .venv
source .venv/bin/activate
pip install -r ../requirements.txt
playwright install chromium
cd ..
```

---

## Running the app

### Dashboard

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

### Running a refresh

Click **Refresh Now** in the dashboard, or run directly:

```bash
cd agents
source .venv/bin/activate
python orchestrator.py
```

This runs the full pipeline: scrape all sources → refresh price cache → retrain ML model → score all listings. Takes 5–15 minutes depending on site response times and the number of new listings.

### Forcing a scrape (bypass 6h cooldown)

```bash
python agents/sourcing_agent.py --force
```

### Retraining the ML model without re-scraping

```bash
cd agents && python3 -c "
from dotenv import load_dotenv; load_dotenv('../.env.local')
from tools.price_model import train; train()
"
```

---

## Project structure

```
carhunt/
├── app/                        # Next.js App Router
│   ├── api/                    # API routes (listings, refresh, query, settings, logs)
│   └── page.tsx                # Dashboard entry point
├── components/                 # React components (Dashboard, ListingCard, ListingDetail)
├── lib/                        # Shared TypeScript (DB client, types, vehicle-tags)
├── agents/                     # Python scraping + ML pipeline
│   ├── orchestrator.py         # Entry point: Sourcing → Price cache → Retrain → Score
│   ├── sourcing_agent.py       # Scrapes all sources, diffs against DB, enriches new listings
│   ├── analysis_agent.py       # Scores listings with GB model → listing_scores
│   ├── query_agent.py          # Natural-language query CLI (spawned by /api/query)
│   ├── training_collector.py   # Collects broad market data for ML training
│   ├── backfill_training.py    # Targeted fix/rescrape of training data for one model
│   ├── cars.json               # Target vehicles + trim allowlists
│   └── tools/
│       ├── scrapers.py         # Playwright scrapers: CarGurus, Cars.com, CarMax, Carvana
│       ├── dealer_scraper.py   # Playwright + API scraper for dealer websites
│       ├── db.py               # psycopg2 helpers
│       ├── scoring.py          # score_listing() + drivetrain/powertrain detection
│       ├── price_model.py      # Stacked ensemble fair-price estimator (GB + KNN + Ridge)
│       ├── base_trims.py       # Trim allowlist + canonical_trim normalization (LLM fallback)
│       └── log_tee.py          # stdout/stderr tee → /tmp/carhunt-refresh.log
├── db/
│   └── schema.sql              # PostgreSQL schema
├── docker-compose.yml          # PostgreSQL container
└── .env.local                  # API keys and config (not committed)
```

---

## How the Value Score works

Each listing is scored 0–100 using `sigmoid((fair_price − listing_price) / fair_price × 11)`.

The fair price comes from a **Stacked Ensemble** (blended `HistGradientBoostingRegressor` + `KNeighborsRegressor` + `Ridge` regression, or standard `HistGradientBoostingRegressor` fallback for the global model) trained on broad market data for each target vehicle. Features: year, log-transformed mileage (`log1p(mileage)`), trim premium (dollar premium relative to base trim MSRP), drivetrain (FWD/AWD), powertrain (gas/hybrid/PHEV), and CPO status.

| Score | Category |
|---|---|
| 88+ | Exceptional Deal |
| 72–88 | Great Deal |
| 58–72 | Good Deal |
| 45–58 | Fair Price |
| < 40 | Poor Value |

---

## Troubleshooting

**Scraper returns 0 results for a source**
- Check `refresh_log.source_errors` in the DB or dashboard logs
- Set `headless=True` → `headless=False` in `agents/tools/scrapers.py` to watch the browser live
- Residential IP is the main defence against bot detection — VPNs may cause blocks

**Dashboard shows "No listings here yet"**
- Run `python agents/orchestrator.py` manually to populate the DB

**Database connection error**
- Make sure Docker is running: `docker compose up -d`
- Check the container: `docker compose ps`
