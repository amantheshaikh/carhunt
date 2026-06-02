# CarHunt

Weekly used car deal tracker. Scrapes CarGurus, AutoTrader, CarMax, and Carvana every Monday, scores every listing by value-for-money, and surfaces the top deals in a local dashboard.

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
```

Get an OpenRouter key at [openrouter.ai](https://openrouter.ai). The app uses `google/gemma-4-31b-it:free` by default — no credit card required.

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

Click **Refresh Now** in the dashboard, or run directly from the terminal:

```bash
cd agents
source .venv/bin/activate
python orchestrator.py
```

This runs the full pipeline: scrape all 4 sources → score every listing → write results to DB. Takes 2–5 minutes depending on site response times.

---

## Project structure

```
carhunt/
├── app/                  # Next.js App Router
│   ├── api/              # API routes (listings, contact, refresh, settings)
│   └── page.tsx          # Dashboard entry point
├── components/           # React components (Dashboard, ListingCard, ListingDetail)
├── lib/                  # Shared TypeScript (DB client, types)
├── agents/               # Python agents
│   ├── orchestrator.py   # Sequences sourcing → analysis
│   ├── sourcing_agent.py # Scrapes all 4 sources via Playwright
│   ├── analysis_agent.py # Scores every listing (Value Score 0–100)
│   ├── communication_agent.py  # Generates inquiry text, logs contacts
│   └── tools/            # Shared utilities (DB, scrapers, scoring, reliability)
├── db/
│   └── schema.sql        # Full PostgreSQL schema
├── docker-compose.yml    # PostgreSQL container
└── .env.local            # API keys (not committed)
```

---

## How the Value Score works

Each listing is scored 0–100 based on:

| Component | Weight | Notes |
|---|---|---|
| Price vs. market | 30% | How far below median market price |
| Mileage | 25% | Lower mileage = higher score |
| Paperwork | 20% | Clean title + no accidents = 100 |
| Reliability | 15% | Static lookup table by make/model |
| Age | 10% | Newer = higher score |

Green badge ≥ 70 · Yellow 40–69 · Red < 40

---

## Contacting a dealer

1. Click **Contact Dealer** on any listing
2. The pre-filled inquiry is copied to your clipboard automatically
3. The listing URL opens in a new tab — paste the inquiry into the dealer's contact form
4. The contact attempt is logged in the dashboard

Edit the default inquiry template in **Settings**.

---

## Troubleshooting

**Scraper returns 0 results for a source**
- Check `refresh_log.source_errors` in the DB or dashboard logs
- Run with `headless=False` in `agents/tools/scrapers.py` → `_new_stealth_page()` to watch the browser live
- Your residential IP is the main defence against bot detection — VPNs may cause blocks

**Dashboard shows "No listings here yet"**
- Run `python orchestrator.py` manually to populate the DB

**Database connection error**
- Make sure Docker is running: `docker compose up -d`
- Check the container: `docker compose ps`
