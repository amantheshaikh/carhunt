# CarHunt

Used-car deal tracker powered by machine learning and automated web scraping. Scrapes CarGurus, Cars.com, and local dealer websites for target vehicle configurations, scores each listing with a stacked ensemble ML fair-price model, and surfaces the best deals in an authenticated Next.js dashboard.

---

## Prerequisites

- **Docker Desktop** (for PostgreSQL)
- **Node.js** v18+ and npm
- **Python** 3.11+
- An API key from [OpenRouter](https://openrouter.ai) (uses `deepseek/deepseek-v4-flash` for insights, trim normalization, and chat queries)

---

## Quickstart (Under 5 Minutes)

### 1. Run the setup script

Clone the repo and run:

```bash
./setup.sh
```

This single command:
1. Verifies prerequisites (Docker, Node, Python).
2. Generates `.env.local` from `.env.example`.
3. Starts PostgreSQL in Docker and automatically sets up tables and dealer seeds.
4. Installs Node.js dependencies (`npm install`).
5. Creates a Python virtual environment in `agents/.venv` and installs dependencies + Playwright Chromium.

### 2. Add your OpenRouter API key

Open `.env.local` and add your OpenRouter key:

```env
OPENROUTER_API_KEY=your_key_here
```

*(Optional: adjust `DASHBOARD_USERNAME` or `DASHBOARD_PASSWORD` if you'd like custom login credentials.)*

### 3. Launch the dashboard

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) and log in:
- **Username:** `admin` (or what you set in `.env.local`)
- **Password:** `carhunt`

Pre-trained fair price models (`agents/tools/price_model_*.pkl`) are included out-of-the-box, so vehicles will be scored immediately when scraped!

---

## Running a Refresh

To fetch listings and score them:
- Click **"Refresh Now"** in the dashboard header, or
- Run directly from the command line:

```bash
cd agents && source .venv/bin/activate && python orchestrator.py
```

The refresh pipeline runs in 4 stages:
1. **Sourcing** (`sourcing_agent.py`) — Scrapes CarGurus, Cars.com, and dealer sites, validates trims, and extracts detail page insights.
2. **Price Cache** — Rebuilds market median benchmarks.
3. **Model Retrain** — Refits the stacked ensemble if new broad training data exists.
4. **Scoring** (`analysis_agent.py`) — Evaluates all unscored listings using the ML model.

Takes ~5–15 minutes depending on site response times.

---

## Customizing for Your City & Vehicles

### 1. Changing Your Location (ZIP Code & Radius)

Search parameters are stored in the `user_settings` database table. To change your ZIP code or search radius:

```bash
docker compose exec db psql -U carhunt -d carhunt -c "UPDATE user_settings SET zip_code = '30328', radius_miles = 50;"
```

CarGurus and Cars.com scrapers will automatically use your new ZIP code and search radius on the next refresh.

### 2. Changing Tracked Vehicles

Target vehicles and trim allowlists are configured in [`agents/cars.json`](agents/cars.json).

Each entry defines:
- `make` and `model`
- `cargurus_id` (model page ID) and `cargurus_search_path`
- `min_year` / `max_year`
- `trims` (allowed trims to track)
- `msrp` table (used for trim-premium calculations)

---

## Project Structure

```
carhunt/
├── app/                        # Next.js App Router
│   ├── api/                    # REST endpoints (listings, refresh, query, logs)
│   └── page.tsx                # Dashboard entry point
├── components/                 # React UI components (Dashboard, ListingCard, Detail, Chat)
├── lib/                        # Shared TypeScript helpers (db client, agents config, vehicle tags)
├── db/
│   ├── schema.sql              # PostgreSQL schema (tables, constraints, indexes)
│   └── seed_dealers.sql        # Curated dealer configurations and scrapers
├── agents/                     # Python scraping + ML pipeline
│   ├── orchestrator.py         # Refresh pipeline entry point
│   ├── sourcing_agent.py       # Scrapers coordinator & listing enrichment
│   ├── analysis_agent.py       # Stacked ensemble scoring engine
│   ├── query_agent.py          # Natural-language SQL chat assistant
│   ├── cars.json               # Vehicle configuration & trim allowlists
│   └── tools/
│       ├── price_model_*.pkl   # Pre-trained stacked ensemble model weights
│       ├── price_model.py      # HistGradientBoosting + KNN + Ridge stacked estimator
│       ├── scrapers.py         # Playwright scrapers (CarGurus, Cars.com)
│       └── dealer_scraper.py   # Dealer inventory scrapers
├── setup.sh                    # One-command automated onboarding script
├── docker-compose.yml          # PostgreSQL database container
└── .env.example                # Sample environment configuration
```

---

## How the Value Score Works

Each listing is scored 0–100 using a per-model sigmoid over price residuals:

$$\text{score} = \sigma\left(\frac{\text{fair\_price} - \text{listing\_price}}{\text{residual\_std}} \times 0.94\right) \times 100$$

A score of ~72 represents ~1 standard deviation below fair market price (Great Deal), and 88+ represents ~2 standard deviations below market (Exceptional Deal).

| Score | Category |
|---|---|
| 88+ | Exceptional Deal |
| 72–88 | Great Deal |
| 58–72 | Good Deal |
| 45–58 | Fair Price |
| < 40 | Poor Value |

The fair price is computed using a **Stacked Ensemble** (`HistGradientBoostingRegressor` + `KNeighborsRegressor` + `Ridge`) trained on vehicle age, log mileage, trim MSRP premium, drivetrain (FWD/AWD), powertrain (gas/hybrid/EV), and CPO status.

---

## Troubleshooting

- **Scraper returns 0 results:**
  CarGurus and Cars.com use bot detection. Running from your home residential IP is the most effective approach. Avoid data-center VPNs during scraping.
- **Database connection error:**
  Ensure Docker is running: `docker compose ps`. Restart if needed: `docker compose up -d`.
- **Resetting your local database:**
  To start completely fresh: `docker compose down -v && docker compose up -d`.
