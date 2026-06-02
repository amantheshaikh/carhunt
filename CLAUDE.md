# CarHunt

A personal used-car deal tracker for the Atlanta metro area. Scrapes CarGurus, Cars.com, CarMax, Carvana, and local dealer websites for 10 target vehicle configurations, scores each listing using an ML fair-price model, and surfaces the best deals in a Next.js dashboard.

---

## Architecture

```
carhunt/
├── app/                      # Next.js app router (UI + API)
│   └── api/                  # REST endpoints called by the dashboard
├── components/               # React UI components
├── lib/                      # DB client (pg), TypeScript types, vehicle-tags helpers
├── agents/                   # Python scraping + ML pipeline
│   ├── orchestrator.py       # Entry point: Sourcing → Model retrain → Scoring
│   ├── sourcing_agent.py     # Scrapes all sources, diffs against DB, enriches new listings
│   ├── analysis_agent.py     # Scores unscored listings (pure ML, no LLM)
│   ├── communication_agent.py# Spawned by /api/contact to log outreach
│   ├── query_agent.py        # Natural-language query CLI (spawned by /api/query)
│   ├── training_collector.py # Collects broad market data for ML training
│   ├── cars.json             # Single source of truth: target vehicles + trim rules
│   └── tools/
│       ├── scrapers.py       # Playwright scrapers: CarGurus (new + legacy), Cars.com, CarMax, Carvana
│       ├── dealer_scraper.py # Playwright inventory scraper for dealer websites
│       ├── db.py             # psycopg2 helpers + refresh_self_computed_medians()
│       ├── scoring.py        # score_listing() + detect_drivetrain/powertrain
│       ├── price_model.py    # HistGradientBoostingRegressor fair-price estimator
│       └── base_trims.py     # Vehicle config loader, _find_vehicle, canonical_trim
```

---

## Target Vehicles (`agents/cars.json`)

| Make | Model | CarGurus ID | Allowed Trims | Year Constraints |
|---|---|---|---|---|
| Hyundai | Tucson Hybrid | d3141 | SEL Convenience, N Line (≤2025), Limited | 2025–2026 |
| Kia | Sportage Hybrid | d3239 | EX, X-Line, SX Prestige | 2026 only |
| Volkswagen | Tiguan | d1104 | SEL R-Line, SE R-Line Black | 2025–2026 |
| Buick | Envision | d2398 | Preferred, Sport Touring, Avenir | 2024–2025 |
| BMW | X1 | d2160 | xDrive28i (always AWD) | 2023–2025 |
| BMW | X3 | d392 | sDrive30i (≤2024), xDrive30i | 2023–2025 |
| Lexus | NX 350h | d2294 | Premium, Luxury, F SPORT | 2022–2024 |
| Volvo | XC40 | d2624 | Plus, Ultimate (B4 FWD or B5 AWD) | 2023–2025 |
| Volvo | XC60 | d1629 | Plus, Ultimate (B5 gas or Recharge T8 PHEV) | 2023–2025 |

**Search params** (stored in `user_settings` DB table):
- Zip: 30328 · Radius: 100 mi · Max price: $45k · Max mileage: 30k · Years: 2025–2026

**CarGurus IDs** are model-specific page identifiers for the legacy URL format. The new search format uses `cargurus_search_path` (make/model/trim paths encoded as `m{makeId}/d{modelId}/trimName`).

**Make IDs:** Hyundai=m28, Kia=m33, VW=m55, Buick=m21, BMW=m3, Lexus=m37, Volvo=m56

**Notes:**
- BMW X1 2023–2025: xDrive28i only (always AWD). sDrive28i and M35i excluded.
- BMW X3 2023–2025: sDrive30i (RWD, ≤2024 only), xDrive30i (AWD). M40i excluded.
- Lexus NX 350h 2022–2024: always AWD. Trim allowlist filters out NX 450h+ variants.
- Volvo XC40: B4 = FWD, B5 = AWD (detected from trim text). No Recharge electric.
- Volvo XC60: includes both B5 gas/MHEV (AWD) and Recharge T8 PHEV (eAWD). `detect_powertrain` uses "recharge" keyword to classify PHEV.
- Year range in `user_settings` is the global floor; per-model `min_year`/`max_year` in cars.json take precedence.

**Adding a new vehicle:**
1. Add entry to `cars.json` with `make`, `model`, `cargurus_id`, `cargurus_search_path`, `trims`, `min_year`, optional `max_year`
2. Add trim levels to `_TRIM_LEVELS` in `price_model.py`
3. Add platform min year to `_PLATFORM_MIN_YEAR`
4. Add AWD override to `_DRIVETRAIN_DEFAULTS` if always AWD
5. Add to `_TRIM_DRIVETRAIN` if a specific trim has a known drivetrain

---

## Scraping Pipeline

**Trigger:** Dashboard "Refresh Now" → `POST /api/refresh` → spawns `orchestrator.py` as a detached process.

**Phases:**
1. **Sourcing** (`sourcing_agent.py`) — 3-pass ingestion per source
2. **Price model retrain** — `tools/price_model.py train()` rebuilds estimators on fresh data
3. **Scoring** (`analysis_agent.py`) — scores every unscored listing (no LLM, pure ML)

**3-pass ingestion:**
- **Pass 1** — Filter candidates: `_is_valid()` + VIN dedup + URL dedup + fingerprint (year/mileage/price) dedup
- **Pass 2** — Detail page enrichment: fetches each new listing's page to extract accident count, title status, owner count, and LLM qualitative insights. **Only runs for CarGurus and Cars.com** — CarMax, Carvana, and dealer sources set these fields directly in their scrapers.
- **Pass 3** — Re-validate only accident/title fields (the rest already passed Pass 1), save or skip

**Validation gates** (`_is_valid`):
- Salvage/rebuilt/flood/lemon/fleet title → reject
- Any accident on record → reject
- Price < $8k or > max_price → reject
- Mileage > max_mileage (used) → reject
- Year outside [min_year, max_year] → reject
- Model not in `cars.json` → reject
- Trim not in allowed list for that model → reject
- Home delivery/out-of-GA listings → reject (`shouldFilterByState=true` at URL level for new CarGurus format; post-scrape state check for other sources)

**CarGurus scraping (new search format):**
- All models with `cargurus_search_path` use the new `/search?srpVariation=DEFAULT_SEARCH` format
- `makeModelTrimPaths` provides trim-level pre-filtering at the URL
- `vehicleHistoryOptions=CLEAN_TITLE,LEMON_FREE,NON_SALVAGE,NON_FLEET,NO_THEFT_HISTORY,UNDAMAGED_FRAME`
- Sourcing: `isDeliveryEnabled=false`, `shouldFilterByState=true`, `distance=100`
- Training: `isDeliveryEnabled=true`, no state filter, `distance=2500` (nationwide)
- All models share ONE browser session per scrape run (not one per model)
- Pagination: `&page=N`, page size detected dynamically, up to 100 pages

**Trim normalization** (`canonical_trim` in base_trims.py):
- Pass 1: prefix match — "xDrive30i AWD" → "xDrive30i"
- Pass 2: BMW token-reorder — "30 xDrive" → "xDrive30i"
- Pass 3: whole-word substring — "B5 Plus Dark Theme AWD" → "Plus"
- Engine prefix stripping: "350h Premium AWD" → "Premium", "2.0T SE R-Line Black" → "SE R-Line Black"

**Model filtering per source:**
- **CarGurus**: new search format for models with `cargurus_search_path` (all current models). Legacy `/Cars/l-Used-...` format only for models without (currently none).
- **Cars.com**: paginated (`&page=N`, up to 10 pages). Uses `&no_accidents=true&clean_title=true&personal_use=true`.
- **Carvana**: base model name only, nationwide, no radius.
- **CarMax**: title-case base model name, radius filter.
- **Dealers**: full inventory scraped, `_is_valid()` filters post-scrape.

**Blocked sources:** AutoTrader (Akamai Bot Manager) and Edmunds (IP-level 403) cannot be scraped with Playwright.

---

## Scoring & Deal Categories

**Score = sigmoid((ML_fair_price − listing_price) / ML_fair_price × 25)**

| Score | Deal Category | Meaning |
|---|---|---|
| 88+ | Exceptional Deal | ~18%+ below fair value |
| 72–88 | Great Deal | ~10–18% below fair value |
| 58–72 | Good Deal | ~5–10% below fair value |
| 45–58 | Fair Price | Near fair value |
| < 40 | Auto-expired | Overpriced — removed from active listings |

Scores are displayed as human-readable deal categories in the dashboard (not raw numbers). The ML model encodes year, mileage, trim, drivetrain, and powertrain into the predicted fair price — no LLM involved in scoring.

---

## Qualitative Insights (LLM)

During detail-page enrichment (Pass 2), an LLM call extracts qualitative signals from the listing page text:
- **CPO** — explicitly Certified Pre-Owned
- **Packages** — named option packages (e.g. "M Sport Package", "Premium Package")
- **Price drop** — dollar amount if a reduction is mentioned
- **Owner notes** — short string for notable condition info (e.g. "1-owner, all service records")

Stored in the `insights` JSONB column on `listings`. Displayed as tags on listing cards and in the detail panel. Uses `deepseek/deepseek-v4-flash` via OpenRouter. Only populated for new CarGurus/Cars.com listings (not dealer/Carvana/CarMax).

---

## ML Price Model (`tools/price_model.py`)

- **Algorithm:** `HistGradientBoostingRegressor`, max_iter=200, learning_rate=0.05
- **Monotone constraints:** mileage↑→price↓, year↑→price↑, trim↑→price↑, AWD>FWD, hybrid/PHEV>gas
- **Features:** year, mileage bucket (0–15k/15–30k/30–60k/60k+), trim_level (ordinal), drivetrain (0/0.5/1), powertrain (gas=0, hybrid=0.8, PHEV/electric=1.0)
- **Mileage is bucketed** (not continuous) to prevent over-rewarding low-mileage differences
- **One model per vehicle** — no cross-model contamination
- **Training data:** P10 percentile floor applied per model-year to cut accident/salvage tail prices
- **Year constraints:** `_MODEL_YEAR_RANGE` from cars.json enforces target year floor/ceiling (e.g. Tiguan trains only on 2025–2026 data)
- **Platform min-year filters** — X1 2023+ (U11 platform); NX 350h 2022+ (TNGA-K gen); XC40/XC60 2023+
- **Rental filter:** rows with >14k mi/year excluded; $20k price floor
- **Powertrain in training data:** stored in `price_training_data.powertrain` column, detected from original (pre-canonical) trim at insert time. XC60 Recharge T8 → `phev`; B5 → `gas`.
- **Saved to:** `agents/tools/price_model_{slug}.pkl` — rebuilt on every refresh and via `/retrain`
- **Minimum rows:** 30 per model to fit; falls back to `_global` estimator otherwise

**Drivetrain overrides** (`_DRIVETRAIN_DEFAULTS` — model+year level, `_TRIM_DRIVETRAIN` — trim level):
- 2025+ Hyundai Tucson Hybrid → always AWD
- 2025+ Buick Envision → always AWD
- 2026+ Kia Sportage Hybrid → always AWD
- 2023+ Lexus NX 350h → always AWD
- 2023+ BMW X1 → always AWD (xDrive28i only)
- 2025+ BMW X3 → always AWD (xDrive30i only)
- 2023+ Volvo XC60 → always AWD (B5/B6/T8 all AWD)
- VW Tiguan SEL R-Line → always AWD (trim-level override in `_TRIM_DRIVETRAIN`)
- VW Tiguan SE R-Line Black → detect from listing (FWD or AWD)
- Volvo XC40 → detect from trim (B4=FWD, B5=AWD)

**Drivetrain detection** (`scoring.py`) — keyword-based:
- `xDrive`, `eAWD`, `quattro`, `4MATIC`, `4Motion` → AWD
- `sDrive` → non-AWD (RWD/FWD)

**Powertrain detection** (`scoring.py`):
- `recharge`, `plug-in`, `phev` → PHEV (1.0)
- `hybrid` → Hybrid (0.8)
- `electric`, `ev`, `bev` → Electric (1.0)
- else → Gas (0.0)

---

## `/retrain` Skill

Type `/retrain` in Claude Code to run a broad market sweep and retrain. Scrapes CarGurus (new search format, nationwide), Cars.com, Carvana, and CarMax across 16 stages covering 2023–2026 with no price ceiling, then rebuilds all per-model estimators.

```bash
# Standard (8 stages, 2025-2026, ≤$55k):
python3 training_collector.py

# Broad (16 stages, 2023-2026, no price cap):
python3 training_collector.py --broad
```

**Training data quality gates:** accidents filtered at CarGurus/Cars.com URL level (`vehicleHistoryOptions`) + `_is_clean()` at insert; rental/fleet filtered by >14k mi/yr; $20k price floor; old-platform rows excluded by `_PLATFORM_MIN_YEAR`; out-of-target-year rows excluded by `_MODEL_YEAR_RANGE`; non-allowed trims rejected by `is_trim_allowed()`; powertrain stored from original trim before canonicalization.

---

## Key Files to Edit

| File | When to change |
|---|---|
| `agents/cars.json` | Add/remove models, change trims, update CarGurus IDs or search paths |
| `agents/tools/scoring.py` | Adjust sigmoid steepness (currently 25×), drivetrain/powertrain detection keywords |
| `agents/analysis_agent.py` | Adjust auto-expire score threshold (currently < 40) |
| `agents/tools/price_model.py` | Change ML features, trim rankings, drivetrain overrides, platform min-years |
| `agents/tools/scrapers.py` | Change CarGurus new/legacy format, pagination, anti-detection, LLM insight extraction |
| `agents/tools/base_trims.py` | Change trim normalization logic (canonical_trim passes) |
| `components/ListingCard.tsx` | What's shown on each card |
| `components/ListingDetail.tsx` | What's shown in the detail panel |
| `components/Dashboard.tsx` | Tabs, filter options, sort options |
| `lib/vehicle-tags.ts` | Deal category thresholds and styles |
| `agents/sourcing_agent.py` | Change validation rules or scraping flow |
| `agents/tools/dealer_scraper.py` | Change dealer scraping strategies or per-site timeout (currently 90s) |
| `agents/orchestrator.py` | Change phase timeouts (training: 5 min, scoring: 5 min, watchdog: 30 min) |

---

## Running Locally

```bash
# Prerequisites: Docker running (Postgres runs in Docker)
docker compose up -d db

# Start the UI
cd /path/to/carhunt && npm run dev    # http://localhost:3000

# Trigger a full refresh (scrape + enrich + score)
curl -X POST http://localhost:3000/api/refresh
# Or directly:
cd agents && python3 orchestrator.py

# Force scrape all sources (bypass 6h cooldown)
cd agents && python3 sourcing_agent.py --force

# Collect broad training data + retrain ML
cd agents && python3 training_collector.py --broad
cd agents && python3 -c "from tools.price_model import train; train()"
```

**Environment** (`.env.local`):
```
DATABASE_URL=postgresql://carhunt:carhunt@localhost:5432/carhunt
OPENROUTER_API_KEY=...          # used by orchestrator (summary), query_agent, and LLM insight extraction
AGENTS_DIR=/path/to/carhunt/agents
AGENTS_PYTHON=/path/to/carhunt/agents/.venv/bin/python3
PROXY_URL=...                   # optional: http://user:pass@host:port for residential proxy
```

---

## Database Tables

| Table | Purpose |
|---|---|
| `listings` | All scraped listings (active, expired, contacted, purchased) |
| `listing_scores` | `value_score`, `market_median_price`, `market_price_source`, `shap_values`, `scored_at` per listing |
| `market_price_cache` | Median prices per (make, model, year, mileage_bucket) |
| `price_training_data` | Broader market data for ML model training (includes `powertrain` column) |
| `dealer_sites` | Dealer URLs + `last_scraped_at` cooldown (6h) + `is_active` flag + `scraper_config` |
| `user_settings` | Search params (zip, radius, max_price, max_mileage, min_year, max_year, allowed_models) |
| `refresh_log` | One row per refresh run — timing + per-source stats |
| `contact_log` | Contact events and notes per listing |
| `watchlist` | Saved/favorited listings with `last_price` for price-drop tracking |

---

## Active Dealer Sites (55 total)

**Hyundai (10):** ALM Hyundai Athens, ALM Hyundai West, AutoNation Hyundai Mall of Georgia, Jim Ellis Hyundai, Rick Case Hyundai Atlanta, Rick Case Hyundai Roswell, South Towne Hyundai, and 3 others

**Kia (9):** Bulldog Kia Athens, Carriage Kia Alpharetta, Ed Voyles Kia Chamblee, Jim Ellis Kia, Kia Atlanta South, Lou Sobh Kia, Rick Case Kia, Calavan Kia West, Kia Mall of Georgia

**Volkswagen (6):** Atlanta Volkswagen, Hoover VW Lithia Springs, Jim Ellis Volkswagen Atlanta, Nalley Volkswagen Alpharetta, Stone Mountain Volkswagen, Volkswagen of Marietta

**Buick (5):** Carl Black Buick GMC Roswell, Carl Black Buick GMC Kennesaw, Jim Ellis Buick GMC, Rick Hendrick Buick GMC Duluth, Southtowne Chevrolet Buick GMC

**Cadillac (5):** Classic Cadillac Atlanta, Jim Ellis Cadillac Atlanta, Hennessy Cadillac Duluth, Capital Cadillac Smyrna, SouthTowne Cadillac Newnan

**BMW (6):** Athens BMW, BMW of Gwinnett Place, BMW of South Atlanta, Global Imports BMW Atlanta, Nalley BMW Decatur, United BMW Alpharetta

**Lexus (5):** Butler Lexus South Atlanta, Hennessy Lexus Atlanta, Hennessy Lexus Gwinnett, Nalley Lexus Roswell, Nalley Lexus Smyrna

**Multi-brand (8):** ALM Gwinnett, ALM Kennesaw, ALM Kia South, ALM Mall of Georgia, ALM Marietta, ALM Roswell, Amazing Luxury Cars, Nalley Kia

**Toyota (1):** Heyward Allen Toyota (Athens)

**Inactive:** All Mazda dealers (CX-5 removed); Cadillac sites retained for cross-brand BMW/Lexus discovery

---

## Development Notes

- **Status system:** `new` → `active` → `contacted` → `purchased`. `expired` = gone. Listings auto-expire if score < 40 OR if the user manually marks them expired. Listings are **never** expired just because a scrape didn't find them.
- **No auto-sold:** Scraper never marks listings as sold. User controls this via "Mark Expired".
- **New car scraping is disabled.** Only used/CPO cars.
- **Detail-page enrichment** runs concurrently (3 pages at a time) with a 10-minute total session cap. Only CarGurus and Cars.com require enrichment. LLM insight extraction runs in a background thread per listing (timeout=15s).
- **CarGurus new search format:** uses `data-testid="srp-listing-tile"` DOM elements; all models share one browser session per scrape; `_dismiss_cargurus_modal()` handles the "Only show GA listings?" modal before tile extraction.
- **Canonical trim normalization** (`canonical_trim` in base_trims.py): 3-pass — prefix match → BMW token-reorder → whole-word substring (for Volvo "B5 Plus Dark Theme AWD" → "Plus"). Engine prefix regex strips "350h ", "2.0T " etc.
- **Dealer scraping is parallelized** — 5 sites scraped concurrently via `ThreadPoolExecutor(max_workers=5)`. Each site has a 90s timeout.
- **Dealer scraper strategies** — Playwright: DealerOn `data-vehicle` JSON, DealerOn per-attribute, Dealer.com `li.vehicle-card[data-uuid]`, Algolia InstantSearch, DealerInspire, JSON-LD. API: `overfuel` (ALM), `carscommerce` (CDK), `ridemotive` (Jim Ellis Kia), `dealerinspire_algolia` (BMW/Cadillac via Algolia `SEWJN80HTN`), `cobalt` (Lexus/Toyota — intercepts `getInventory` API).
- **Adding a Cobalt dealer:** set `scraper_config = {"type": "cobalt"}`.
- **Adding a DealerInspire Algolia dealer:** `scraper_config = {"type": "dealerinspire_algolia", "app_id": "SEWJN80HTN", "api_key": "...", "index": "{slug}_production_inventory"}`.
- **CarsCommerce `type_slugs`:** BMW of Gwinnett Place uses `"Pre-Owned"` — set `scraper_config.type_slugs = ["Pre-Owned", "Certified Pre-Owned"]`.
- **Orchestrator timeouts:** 30-minute watchdog; 5-minute cap on training; 5-minute cap on scoring.
- **CarGurus new format pagination** uses `&page=N`; page size detected dynamically from page 1 (varies 22–24 per model).
- **Cars.com pagination** uses `&page=N`, up to 10 pages.
- **Anti-detection:** Browser UA rotates across Chrome 134–136 (macOS + Windows). Viewport randomizes across 5 sizes. CarGurus and Cars.com check for bot-detection pages after each load.
- **`analysis_agent.py` has no LLM.** Scores all unscored listings in a single batch with `::uuid` casts.
- **`query_agent.py`** uses LLM (OpenRouter/deepseek-v4-flash) for natural-language questions about the listing DB.
- **Filter bar is client-side.** All filtering and sorting happens in-memory.
- **Dedup within a run:** `known_urls`, `known_vins`, and `known_fingerprints` fetched once before the source loop, shared and mutated across all sources including dealers.
- **Canonical make/model normalization:** `_CANONICAL_NAMES` in sourcing_agent maps scraped casing to cars.json canonical names. `_find_vehicle()` in base_trims uses `_norm()` (lowercase, strip hyphens/spaces).
- **Postgres runs in Docker** (`docker compose up -d db`). Schema is in `db/schema.sql`.
