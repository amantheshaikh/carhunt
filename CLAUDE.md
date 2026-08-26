# CarHunt

A personal used-car deal tracker for the Atlanta metro area. Scrapes CarGurus, Cars.com, and local dealer websites for target vehicle configurations, scores each listing using a stacked ensemble ML model (GB + KNN + Ridge), and surfaces the best deals in a Next.js dashboard.

---

## Architecture

```
carhunt/
├── app/                      # Next.js app router (UI + API)
│   └── api/                  # REST endpoints called by the dashboard
├── components/               # React UI components
├── lib/                      # DB client (pg), TypeScript types, vehicle-tags helpers
├── agents/                   # Python scraping + ML pipeline
│   ├── orchestrator.py       # Entry point: Sourcing → Price cache → Retrain → Score
│   ├── sourcing_agent.py     # Scrapes all sources, diffs against DB, enriches new listings
│   ├── analysis_agent.py     # Scores listings with stacked ensemble → listing_scores
│   ├── query_agent.py        # Natural-language query CLI (spawned by /api/query)
│   ├── training_collector.py # Collects broad market data for ML training
│   ├── backfill_training.py  # Targeted fix/rescrape of price_training_data for one model
│   ├── cars.json             # Single source of truth: target vehicles + trim rules
│   └── tools/
│       ├── scrapers.py       # Playwright scrapers: CarGurus, Cars.com
│       ├── dealer_scraper.py # Playwright inventory scraper for dealer websites
│       ├── db.py             # psycopg2 helpers + refresh_self_computed_medians()
│       ├── scoring.py        # score_listing() + detect_drivetrain/powertrain
│       ├── price_model.py    # Stacked ensemble fair-price estimator (GB + KNN + Ridge)
│       ├── base_trims.py     # Vehicle config loader, canonical_trim, LLM trim fallback
│       ├── vin_decoder.py    # NHTSA vPIC VIN decoder for trim enrichment on dealer listings
│       ├── log_tee.py        # Shared stdout/stderr tee → /tmp/carhunt-refresh.log
│       └── job_tracker.py    # Job progress tracking for dashboard SSE stream
```

---

## Target Vehicles (`agents/cars.json`)

| Make | Model | CarGurus ID | Allowed Trims | Year Constraints |
|---|---|---|---|---|
| Hyundai | Tucson Hybrid | d3141 | SEL Convenience, N Line, Limited | 2025+ |
| Genesis | GV70 | d3163 | Standard, Select, Advanced, Advanced Plus, Sport Prestige (2.5T only) | 2023–2025 |
| Kia | Sportage Hybrid | d3239 | EX, X-Line, SX Prestige | 2023+ |
| Kia | Sportage PHEV | d3244 | EX, EX Premium, X-Line, X-Line Prestige, SX | 2023–2025 |
| Volkswagen | Tiguan | d1104 | SE, SEL R-Line, SE R-Line Black | 2025 |
| Buick | Encore GX | d2901 | Avenir | 2024+ |
| Buick | Envision | d2398 | Preferred, Sport Touring, Avenir | 2024–2025 |
| BMW | X1 | d2160 | xDrive28i (always AWD) | 2023–2025 |
| BMW | X3 | d392 | sDrive30i (≤2024), xDrive30i | 2022–2025 |
| Cadillac | XT4 | d2673 | Luxury, Premium Luxury, Sport | 2024–2025 |
| Lexus | NX 350h | d2294 | Premium, Luxury, F SPORT | 2022–2024 |
| Volvo | XC40 | d2624 | Plus, Ultimate (B4 FWD or B5 AWD) | 2023–2025 |
| Volvo | XC60 | d1629 | Plus, Ultimate (B5 gas or Recharge T8 PHEV) | 2023–2025 |
| Audi | Q5 | d1988 | Premium Plus, Prestige | 2022–2025 |

**Search params** (stored in `user_settings` DB table):
- Zip: 30328 · Radius: 100 mi · Max price: $45k · Years: 2025–2026
- `max_mileage` is kept but ignored at the URL level — mileage is validated by rate (see Mileage Validation below)

**CarGurus IDs** are model-specific page identifiers. The new search format uses `cargurus_search_path` (make/model/trim paths encoded as `m{makeId}/d{modelId}/trimName`).

**Make IDs:** Hyundai=m28, Genesis=m203, Kia=m33, VW=m55, Buick=m21, BMW=m3, Cadillac=m22, Lexus=m37, Volvo=m56, Audi=m19

**Notes:**
- BMW X1 2023–2025: xDrive28i only (always AWD). sDrive28i and M35i excluded.
- BMW X3 2022–2025: sDrive30i (RWD, ≤2024 only), xDrive30i (AWD). M40i excluded.
- Buick Encore GX: Avenir trim only (top trim, always AWD). 2024+.
- Audi Q5: Premium Plus/Prestige, 2022–2025. FWD or Quattro AWD.
- Lexus NX 350h 2022–2024: always AWD. Trim allowlist filters out NX 450h+ variants.
- Volvo XC40: B4 = FWD, B5 = AWD (detected from trim text). No Recharge electric.
- Volvo XC60: includes both B5 gas/MHEV (AWD) and Recharge T8 PHEV (eAWD). Stored as separate model names `"XC60"` and `"XC60 Recharge"` in the DB. `detect_powertrain` uses "recharge" keyword to classify PHEV.
- Genesis GV70: `engine_filter: ["2.5T"]` — shares a CarGurus page with the 3.5T; filter requires "2.5T" in trim. All 5 trims (Standard through Sport Prestige) are sourced; training_trims matches.
- Kia Sportage PHEV: separate model from Sportage Hybrid — different CarGurus ID (d3244), different trim set. Always AWD.
- Cadillac XT4: sourcing target for 2024–2025 Luxury/Premium Luxury/Sport. Both FWD and AWD variants.
- Year range in `user_settings` is the global floor; per-model `min_year`/`max_year` in cars.json take precedence.

**Adding a new vehicle:**
1. Add entry to `cars.json` with `make`, `model`, `cargurus_id`, `cargurus_search_path`, `trims`, `min_year`, optional `max_year`, `msrp` table (used as trim_premium feature)
2. Add trim level ordinals to `_TRIM_LEVELS` in `price_model.py` (used as fallback when MSRP lookup fails)
3. Add platform min year to `_PLATFORM_MIN_YEAR`
4. Add AWD override to `_DRIVETRAIN_DEFAULTS` if always AWD
5. Add to `_TRIM_DRIVETRAIN` if a specific trim has a known drivetrain

---

## Scraping Pipeline

**Trigger:** Dashboard "Refresh Now" → `POST /api/refresh` → spawns `orchestrator.py` as a detached process.

**Orchestrator phases (4 stages):**
1. **Sourcing** (`sourcing_agent.py`) — 3-pass ingestion per source
2. **Price cache refresh** — `refresh_self_computed_medians()` rebuilds `market_price_cache`
3. **Model retrain** — retrains the stacked ensemble (GB + KNN + Ridge) from `price_training_data`
4. **Scoring** (`analysis_agent.py`) — scores all unscored listings → `listing_scores`

**3-pass ingestion:**
- **Pass 1** — Filter candidates: `_is_valid()` + VIN dedup + URL dedup + fingerprint dedup
- **Pass 2** — Detail page enrichment: fetches each new listing's page to extract accident count, title status, owner count, and LLM qualitative insights. **Runs for all sources** (CarGurus, Cars.com, and local dealer sources).
- **Pass 3** — Re-validate only accident/title fields (the rest already passed Pass 1), save or skip

**Validation gates** (`_is_valid`):
- Salvage/rebuilt/flood/lemon/fleet title → reject
- Any accident on record → reject
- Price < $8k or > max_price → reject
- **Mileage rate** > 14,000 mi/year (based on vehicle age) → reject (rental/fleet heuristic)
- Year outside [min_year, max_year] → reject
- Model not in `cars.json` → reject
- Trim not in allowed list for that model → reject
- Home delivery/out-of-GA listings → reject (`shouldFilterByState=true` at URL level for CarGurus; post-scrape state check for other sources)

**Fingerprint dedup** (`_get_known_fingerprints`):
Returns `(price_fps, dealer_fps)` where:
- `price_fps` = `(year, mileage, price)` — exact match dedup across sources
- `dealer_fps` = `(dealer_name, make, model, year, mileage)` — catches same physical car from dealer + marketplace at slightly different prices

**CarGurus scraping:**
- All models use the new `/search?srpVariation=DEFAULT_SEARCH` format via `cargurus_search_path`
- `vehicleHistoryOptions=CLEAN_TITLE,LEMON_FREE,NON_SALVAGE,NON_FLEET,NO_THEFT_HISTORY,UNDAMAGED_FRAME`
- Sourcing: `isDeliveryEnabled=false`, `shouldFilterByState=true`, `distance=100`
- Training: `isDeliveryEnabled=true`, `shouldFilterByState=false`, `nonShippableBaseline=73`, `distance=50000` (nationwide — large value treated as no distance limit)
- All models share ONE browser session per scrape run
- Pagination: `&page=N`, page size detected dynamically, up to 100 pages

**Trim normalization** (`canonical_trim` in base_trims.py) — 4 passes:
- Pass 1: prefix match — "xDrive30i AWD" → "xDrive30i"
- Pass 2: BMW token-reorder — "30 xDrive" → "xDrive30i"
- Pass 3: whole-word substring — "B5 Plus Dark Theme AWD" → "Plus"
- Pass 4: LLM fallback — maps abbreviations like "PREM PLUS" → "Premium Plus" when the 3 regex passes fail. Results cached in-process + on disk at `.trim_cache.json`. **LLM is NOT used in `is_trim_allowed`** (gating must be regex-only to prevent false positives).

**Blocked sources:** AutoTrader (Akamai Bot Manager) and Edmunds (IP-level 403).

---

## Scoring & Deal Categories

**Scoring formula:** `Score = sigmoid((ML_fair_price − listing_price) / residual_std × 0.94)`
where `residual_std` is the OOF residual std dev from the per-model pickle (floor $600). k=0.94 calibrates so 1σ below fair ≈ 72 (Great Deal), 2σ ≈ 88 (Exceptional Deal).

The same dollar discount is worth more for tightly-priced models (low std) than wide-spread ones.

| Score | Deal Category | Meaning |
|---|---|---|
| 88+ | Exceptional Deal | ~2σ+ below fair value |
| 72–88 | Great Deal | ~1–2σ below fair value |
| 58–72 | Good Deal | ~0.5–1σ below fair value |
| 45–58 | Fair Price | Near fair value |
| < 40 | Poor Value | Overpriced relative to fair value |

Score < 40 is a display category only — listings are **never** auto-dismissed by score.

---

## ML Model — Stacked Ensemble (`tools/price_model.py`)

- **Architecture:** Stacked Ensemble of three base estimators (`HistGradientBoostingRegressor` + `KNeighborsRegressor` + `Ridge`), blended via a meta Ridge learner with Out-Of-Fold (OOF) cross-validation blend weights.
- **Monotone constraints (applied to GB):** mileage↑→price↓, year↑→price↑, trim_premium↑→price↑, AWD>FWD, hybrid/PHEV>gas, CPO>non-CPO
- **Features:** year, log-transformed mileage (`log1p(mileage)`), trim_premium (dollar premium relative to base trim MSRP), drivetrain (0/0.5/1), powertrain (gas=0, hybrid=0.8, PHEV/electric=1.0), cpo (0/1)
- **Output:** `listing_scores` — `value_score` (0–100), `fair_price`, `shap_values` (JSONB containing base_value, predicted, features, `gb_pred`, `knn_pred`, `ridge_pred`, and `meta_weights`)
- Trained per vehicle slug (e.g. `price_model_tiguan.pkl`). Models with < 30 training rows are skipped and those listings show as unscored.

**SHAP values** (calculated on the GB component) stored alongside individual estimator predictions and weights for detailed waterfall and ensemble influence visualization.

**Training data quality gates:** accidents filtered at CarGurus/Cars.com URL level (`vehicleHistoryOptions`) + `_is_clean()` at insert; rental/fleet filtered by >14k mi/yr; $20k price floor; old-platform rows excluded by `_PLATFORM_MIN_YEAR`; out-of-target-year rows excluded by `_MODEL_YEAR_RANGE`; non-allowed trims rejected by `is_trim_allowed_for_training()`.

**Drivetrain overrides** (`_DRIVETRAIN_DEFAULTS` — model+year level, `_TRIM_DRIVETRAIN` — trim level):
- 2025+ Hyundai Tucson Hybrid → always AWD
- 2025+ Buick Envision → always AWD
- 2026+ Kia Sportage Hybrid → always AWD
- 2023+ Lexus NX 350h → always AWD
- 2023+ BMW X1 → always AWD (xDrive28i only)
- 2025+ BMW X3 → always AWD (xDrive30i only)
- 2023+ Volvo XC60 → always AWD (B5/T8 all AWD)
- VW Tiguan SEL R-Line → always AWD (trim-level override)
- VW Tiguan SE R-Line Black, Volvo XC40 → detect from listing

**Drivetrain detection** (`scoring.py`) — space-padded keyword-based (handles boundaries):
- `xDrive`, `eAWD`, `quattro`, `4MATIC`, `4Motion` → AWD
- `sDrive` → non-AWD
- `" b6 "` → AWD standard
- `" b4 "` → FWD only

`_drivetrain_for()` in `price_model.py` (and `training_collector._insert`) also appends the listing URL slug (hyphens and slashes replaced with spaces) to the raw text before calling `detect_drivetrain`. This catches FWD/AWD encoded in dealer URL paths when the raw listing text doesn't include a drivetrain keyword.

**Powertrain detection** (`scoring.py`) — space-padded keyword-based (handles boundaries):
- `recharge`, `plug-in`, `phev`, `" t8 "` → PHEV (1.0)
- `hybrid`, `350h`, `400h`, `500h` → Hybrid (0.8)
- `electric`, `" ev "`, `bev`, `ioniq` → Electric (1.0)
- else → Gas (0.0)

---

## XC60 Gas / PHEV Split

XC60 gas (B5) and PHEV (Recharge T8) share the same CarGurus model page, stored as separate DB model names: `"XC60"` for gas and `"XC60 Recharge"` for PHEV. The split is handled by `normalize_listing_model()` in `base_trims.py` (called from `sourcing_agent._save_new` and `training_collector._insert`), which applies `split_xc60_model` then canonical casing. The `allowed_norm` allowlist check must run **before** the split (against the original `"xc60"` name).

---

## Qualitative Insights (LLM)

During detail-page enrichment (Pass 2), an LLM call extracts qualitative signals from the listing page text:
- **CPO** — explicitly Certified Pre-Owned
- **Packages** — named option packages (e.g. "M Sport Package")
- **Price drop** — dollar amount if a reduction is mentioned
- **Owner notes** — short string for notable condition info (e.g. "1-owner, all service records")

Stored in the `insights` JSONB column on `listings`. Uses `deepseek/deepseek-v4-flash` via OpenRouter. Populated for new CarGurus, Cars.com, and dealer listings. Input text is pre-processed via `_focus_listing_text()` — collapses whitespace, builds a window covering all keyword matches (first match −200 chars to last match +2000 chars), with a fallback to the last-match anchor when the span exceeds 4000 chars.

**Trim correction** — when `allowed_trims` is provided, the LLM is also asked to identify the trim from the allowlist. The result is hard-validated post-LLM (case-insensitive exact match against `cars.json` trims); any value that doesn't match is discarded. Trim correction only fires when the listing's current trim is `None` or fails `is_trim_allowed`.

---

## Dashboard Log Streaming

All agents write to `/tmp/carhunt-refresh.log` via `tools/log_tee.py`. The dashboard SSE stream (`/api/logs/stream`) tails this file every 500ms and strips ANSI codes. All `print()` calls in agents must use `flush=True`.

`enable_log_tee()` is called at startup in `orchestrator.py` and `training_collector.main()`.

---

## Training Data Collection

### `training_collector.py`
Staged nationwide scrapes to populate `price_training_data`.

```bash
# Standard (8 stages, 2025-2026, ≤$55k):
python3 training_collector.py

# Broad (16 stages, 2023-2026, no price cap):
python3 training_collector.py --broad
```

**`_stage_groups(vehicles, stage_min_year)`** — groups models by effective min year. Models whose `model_min > stage_min_year` are skipped entirely. `cg_scraped` dedup set tracks already-scraped year+model groups to avoid redundant CarGurus fetches.

### `backfill_training.py`
Targeted fix for a single model.

```bash
# Rederive (fast, no network) — fixes drivetrain/powertrain/canonical trim in-place:
python3 backfill_training.py --model xc60

# Rescrape (slow) — re-collects from all sources, ON CONFLICT DO UPDATE (non-destructive):
python3 backfill_training.py --model xc60 --mode rescrape --retrain

# Dry run:
python3 backfill_training.py --model "tucson hybrid" --dry-run
```

---

## `/retrain` Skill

Type `/retrain` in Claude Code to run a broad market sweep and retrain the model.

---

## Key Files to Edit

| File | When to change |
|---|---|
| `agents/cars.json` | Add/remove models, change trims, update CarGurus IDs or search paths |
| `agents/tools/scoring.py` | Sigmoid formula (k=0.94, std floor $600), drivetrain/powertrain detection keywords |
| `agents/analysis_agent.py` | Stacked ensemble scoring — `listing_scores` |
| `agents/tools/price_model.py` | Stacked ensemble, MSRP-based trim_premium, CPO feature, drivetrain overrides, platform min-years |
| `agents/tools/scrapers.py` | CarGurus format, pagination, anti-detection, LLM insight extraction |
| `agents/tools/base_trims.py` | Trim normalization, LLM fallback, `_CANONICAL_NAMES`, `normalize_listing_model()` |
| `agents/tools/log_tee.py` | Dashboard log file path, tee behavior |
| `agents/sourcing_agent.py` | Validation rules, fingerprint dedup, scraping flow |
| `agents/tools/dealer_scraper.py` | Dealer scraping strategies, per-site timeouts, cobalt_dom JSON-LD fallback, llm_extract |
| `agents/tools/vin_decoder.py` | NHTSA VIN decoder — trim allowlist, cache size |
| `agents/orchestrator.py` | Phase timeouts (training: 5 min, scoring: 5 min, watchdog: 60 min) |
| `components/ListingCard.tsx` | What's shown on each card |
| `components/ListingDetail.tsx` | Detail panel — SHAP waterfall, score display |
| `components/Dashboard.tsx` | Tabs, filter options, sort options |
| `lib/vehicle-tags.ts` | Deal category thresholds and styles |

---

## Running Locally

```bash
# Prerequisites: Docker running (Postgres runs in Docker)
docker compose up -d db

# Start the UI
cd /path/to/carhunt && npm run dev    # http://localhost:3000

# Expose dashboard publicly via Cloudflare quick tunnel (URL changes each run)
cloudflared tunnel --url http://localhost:3000
# Current tunnel: https://athletes-lines-parcel-episode.trycloudflare.com (ephemeral)

# Trigger a full refresh (scrape + enrich + score)
curl -X POST http://localhost:3000/api/refresh
# Or directly:
cd agents && python3 orchestrator.py

# Force scrape all sources (bypass 6h cooldown)
cd agents && python3 sourcing_agent.py --force

# Collect broad training data + retrain model
cd agents && python3 training_collector.py --broad

# Retrain model only (no re-scraping)
cd agents && python3 -c "
from dotenv import load_dotenv; load_dotenv('../.env.local')
from tools.price_model import train; train()
"
```

**Environment** (`.env.local`):
```
DATABASE_URL=postgresql://carhunt:carhunt@localhost:5432/carhunt
OPENROUTER_API_KEY=...          # LLM: insight extraction, trim normalization, query agent, summary
AGENTS_DIR=/path/to/carhunt/agents
AGENTS_PYTHON=/path/to/carhunt/agents/.venv/bin/python3
PROXY_URL=...                   # optional: http://user:pass@host:port for residential proxy
```

---

## Database Tables

| Table | Purpose |
|---|---|
| `listings` | All scraped listings (active, expired, contacted, purchased); includes `user_notes TEXT` for personal notes |
| `listing_scores` | Stacked ensemble: `value_score` (0–100), `fair_price`, `shap_values` (JSONB with per-estimator predictions), `scored_at` |
| `market_price_cache` | Median prices per (make, model, year, mileage_bucket) |
| `price_training_data` | Broader market data for ML training (includes `powertrain`, `drivetrain`, `cpo` columns) |
| `dealer_sites` | Dealer URLs + `last_scraped_at` cooldown (6h) + `is_active` + `scraper_config` JSONB |
| `user_settings` | Search params (zip, radius, max_price, max_mileage, min_year, max_year, allowed_models) |
| `refresh_log` | One row per refresh run — timing + per-source stats + LLM summary |
| `contact_log` | Contact events and notes per listing |
| `watchlist` | Saved/favorited listings with `last_price` for price-drop tracking |

> `db/schema.sql` is the initial schema. The running DB is extended via ALTER TABLE migrations; schema.sql may lag.

---

## Active Dealer Sites (71 total)

**Volvo (5):** Dyer & Dyer, North Point, O'Steen Athens, Mall of Georgia, Marietta — all `cobalt_dom` type (switched from `cobalt` when their DDC sites stopped firing `getInventory` XHR and moved to SSR `li.vehicle-card` DOM)

**Hyundai (9):** ALM Athens (`dealerinspire_html`), ALM West (`dealerinspire_html`), AutoNation MoG (`cobalt_dom`), Grand Hyundai Canton (`dealerinspire_html`), Hyundai of Cumming (`secureoffersites`), Hyundai of Kennesaw (`dealeron_cosmos`), Nalley Hyundai (`cobalt_dom`), Potamkin Stone Mountain (`carscommerce`), South Towne (`carscommerce`)

**Kia (8):** Bulldog Athens (`carscommerce`), Calavan Kia West (`cobalt_dom`), Carriage Alpharetta (`carscommerce`), Ed Voyles Chamblee (`dealeron_cosmos`), Kia Atlanta South (`dealeron_cosmos`), Kia Mall of Georgia (`cobalt_dom`), Lou Sobh (`dealerinspire_html`), Sons Kia (`dealerinspire_html`)

**Volkswagen (6):** AutoNation MoG (`cobalt_dom`), Heritage (`dealerinspire_html`), Hoover Lithia Springs (`cobalt_dom`), Nalley Alpharetta (`cobalt_dom`), Stone Mountain (`dealerinspire_html`), VW Marietta (`dealerinspire_html`)

**Buick (4):** Capital Buick GMC (`dealeron_cosmos`), Carl Black Kennesaw (`carscommerce`), Carl Black Roswell (`carscommerce`), Rick Hendrick Duluth (`carscommerce` dealer_id=10597, group-wide covers all RH Atlanta brands — `allowed_states` removed since location field is empty in API responses but all dealers are GA-local)

**BMW (6):** Athens (`dealerinspire_html`), Gwinnett (`carscommerce`), South Atlanta (`dealerinspire_html`), Global Imports (`cobalt_dom`), Nalley Decatur (`cobalt_dom`), United Alpharetta (`dealerinspire_html`)

**Cadillac (3):** Capital Cadillac Smyrna (`dealeron_cosmos`), Classic Cadillac Atlanta (`cobalt_dom`), SouthTowne Cadillac Newnan (`dealerinspire_html`)

**Lexus (4):** Butler South Atlanta (`cobalt_dom`), Hennessy Gwinnett (`cobalt_dom`), Nalley Roswell (`cobalt_dom`), Nalley Smyrna (`cobalt_dom`) — Hennessy Lexus Atlanta consolidated into Hennessy Auto Group parent

**Genesis (6):** Genesis of Atlanta (`ridemotive`), Genesis of Cumming (`secureoffersites`), Genesis of Gwinnett (`cobalt_dom`), Genesis of Kennesaw (`ridemotive`), Genesis of Union City (`dealerinspire_html`), Rick Case Genesis Roswell (`cobalt_dom`)

**Audi (4):** Atlanta, Gwinnett, South Atlanta, North Atlanta — all `audi_one` type. Inventory URL: `/en/inventory/used/`

**Toyota (4):** Atlanta Toyota (`cobalt_dom`), Cobb County Toyota (generic DOM), Marietta Toyota (generic DOM — Typesense platform, returns 0 for target makes), Nalley Toyota of Roswell (`cobalt_dom`)

**Multi-brand / other (12):** ALM Cars (5 `overfuel` entries: Gwinnett/Kennesaw/MoG/Marietta/Roswell), ALM Mazda South (`overfuel`), Amazing Luxury Cars (generic DOM), Gravity Autos Atlanta (`overfuel`), Hennessy Auto Group (`cobalt_dom` + `progressive_scroll` — Lexus + Cadillac + JLR), Jim Ellis All Brands (`cobalt_dom`), Nalley Kia Multi-Brand (`cobalt_dom`), Rick Case Best Value (`cobalt_dom`)

**ALM Cars:** The group-wide `dealerinspire_algolia` entry (ALM Cars Group) is INACTIVE — its Algolia app_id is deprovisioned. The 5 individual `overfuel` entries (Gwinnett dealer_id=322, Kennesaw=321, MoG=323, Marietta=324, Roswell=326) are ACTIVE and cover the same superstores. ALM Hyundai West (app_id `1WNYBZLEEN`) and ALM Hyundai Athens are `dealerinspire_html` type.

**DealerInspire HTML dealers (13 active):** All 13 DI sites use `scraper_config = {"type": "dealerinspire_html"}`. At runtime, `_scrape_dealerinspire_auto_async` navigates to the inventory page with Playwright, intercepts live API requests via `page.route()` lambda predicate (matching any `carscommerce.inc` or `algolia.net` URL), and harvests fresh credentials from the page's own network traffic. All 13 dealers currently call `POST https://websites-search.api.carscommerce.inc/api/v1/listings/{dealer_id}/search` — the same API as `_scrape_carscommerce_api`. Captured dealer_id and api_key are used to make a direct filtered query (make + year) in lieu of paginating the full DOM. Falls back to DOM scraping (`_scrape_dealer_inventory_async`) if no API request fires within 10s. Inventory URL pattern is `/used-vehicles/` for most sites; Lou Sobh Kia uses `/pre-owned-vehicles/`. Do NOT use `index.htm` in URLs — DealerInspire sites 404 on that path.

**Adding a Cobalt dealer (XHR-based):** set `scraper_config = {"type": "cobalt"}` for DDC sites that still fire a `getInventory` XHR. If a cobalt site stops returning listings, check whether it has migrated to SSR `li.vehicle-card` DOM — if so, switch to `cobalt_dom`.

**Adding a DealerInspire HTML dealer:** set `scraper_config = {"type": "dealerinspire_html"}` and `inventory_url` to the correct path (usually `/used-vehicles/`). No API credentials needed in scraper_config — they are harvested live from the page.

**Adding a DealerInspire Algolia dealer (if app_id is live):** `scraper_config = {"type": "dealerinspire_algolia", "app_id": "...", "api_key": "...", "index": "{slug}_production_inventory"}`.

**CarsCommerce dealers:** BMW of Gwinnett uses `type_slugs = ["Pre-Owned", "Certified Pre-Owned"]` in scraper_config.

**Adding an Audi One dealer:** set `scraper_config = {"type": "audi_one"}` and `inventory_url = "https://www.audi{dealer}.com/en/inventory/used/"`. The scraper handles "Load more vehicles" pagination and extracts VIN, year/make/model from `[data-testid="model-name"]`, trim from `[data-testid="trim-name"]`, and mileage/price from card text.

**Adding a universal LLM-guided dealer:** set `scraper_config = {"type": "llm_extract"}` for any dealer site that resists structured scraping. The scraper loads the page with Playwright, extracts vehicle card text via a multi-pass selector strategy (explicit card selectors → `[data-vin]` parent-walk requiring year+price+mileage → body text fallback), then calls `deepseek/deepseek-v4-flash` (~$0.001/page) to parse structured JSON. Paginates via detected next-page links. Make+year filtering is applied at the LLM level; model/trim filtering happens downstream in `_is_valid`. Use 300s timeout. Best suited for sites with < 15 pages or where URL-level make filtering is available. Note: slower than API-based scrapers (~40s/page vs. ~2s for API).

---

## Development Notes

- **Status system:** `new` → `active` → `contacted` → `purchased`. `dismissed` = user-removed. Listings are **never** auto-dismissed by score or missed scrapes.
- **No auto-sold:** Scraper never marks listings as sold. User controls this via "Mark Expired".
- **Mileage validation is rate-based:** `mileage / years_old > 14,000` → reject. Hard ceiling (`max_mileage`) set to 999999 in `run()` so no cap at the scraper URL level.
- **Detail-page enrichment** runs concurrently (3 pages at a time) with a 10-minute total session cap. CarGurus and Cars.com require enrichment for history parsing, while dealer sites optionally run enrichment for qualitative insights. LLM insight extraction runs in a background thread per listing (timeout=15s).
- **CarGurus new search format** uses `data-testid="srp-listing-tile"` DOM elements; `_dismiss_cargurus_modal()` handles the "Only show GA listings?" modal.
- **Canonical trim normalization** — 4-pass (prefix → BMW token-reorder → whole-word substring → LLM). Engine prefix regex strips "350h ", "2.0T " etc. before matching. To prevent ingestion failures, `is_trim_allowed` also supports Pass 2 (BMW token-reorder).
- **Title parsing LLM fallback** — `_parse_title` in `dealer_scraper.py` falls back to a cached LLM title parser (`deepseek-v4-flash` via OpenRouter) when standard regex/substring matching fails to resolve a valid make/model. Cache stored in `.title_cache.json`. Uses a strict prompt format (`{"matched": false}`) to prevent force-mapping non-target vehicles. `_llm_model_valid_for_title()` post-validates every LLM result: (1) if the matched model has a powertrain qualifier (Hybrid/PHEV/Recharge), the source title must contain one too; (2) all non-powertrain words in the model name must appear in the title. This prevents cross-variant false positives (e.g. "Elantra Hybrid" title → "Tucson Hybrid" match).
- **Dealer scraping is parallelized** — 5 sites concurrently via `ThreadPoolExecutor(max_workers=5)`. `cobalt` (XHR-intercept) and `audi_one` sites have 150s timeout; others 90s.
- **`llm_extract` scraper** — LLM-guided universal extractor for sites that resist DOM/API strategies. Card extraction uses a 3-pass JS approach: (1) explicit card-level CSS selectors, (2) `[data-vin]` parent-walk requiring the ancestor to contain year-pattern + price + mileage text, (3) body-text fallback. Uses `innerText` not `outerHTML` for compactness. LLM prompt filters by make + year; model/trim gating handled by `_is_valid` downstream. Pagination via `_NEXT_PAGE_JS` next-link detection. LLM: `deepseek/deepseek-v4-flash` at ~$0.001/page. Timeout: 300s in dispatcher.
- **`_add_used` coerce** uses `_int()` for price/mileage/year — handles decimal strings via `int(float(...))` to avoid stripping the decimal point into a 100x inflated value.
- **dealerinspire_algolia:** All `dealerinspire_algolia` entries have been deactivated — app_ids are deprovisioned or belong to non-target brands (Mazda/Toyota). The type still exists in the dispatcher for future use if a live Algolia index is found.
- **cobalt_dom pagination:** `_scrape_cobalt_dom_async` tracks `seen_ext_ids` (VIN or href-derived) across pages and breaks immediately when a page yields no new listings. This prevents an infinite loop on DDC sites whose `?start=N` param is silently ignored (e.g. Volvo dealers that redirect to a fixed URL).
- **cobalt_dom soft time limit:** `_t0` is set before browser launch (line ~280) so the 100s soft limit is measured from total wall time including browser setup. This ensures ≥50s remains for `browser.close()` within the `asyncio.wait_for(timeout=150)` budget — preventing a race where close() gets cancelled and partial results are lost.
- **cobalt_dom title prefix:** `li.vehicle-card` body text may read "Used 2025 Hyundai..." — the JS evaluate block strips "Used|Pre-Owned|Certified|New" prefixes before the year-pattern match so titles are correctly parsed.
- **cobalt_dom JSON-LD fallback:** DDC sites like Dyer & Dyer embed full inventory in `<script type="application/ld+json">` structured data, but only render `.price-value` DOM elements for a subset of cards (transparent-pricing sites show the fee-inclusive "Transparent Price" in structured data, not the base vehicle price in the DOM). Before the card loop, a recursive `_jldFind()` JS function builds a VIN→{price, mileage, url} map from all JSON-LD blocks on the page. When a card lacks a DOM price, the JSON-LD entry for that VIN is used as fallback. JSON-LD prices on transparent-pricing sites include dealer fees (~$1–2k above base); this is acceptable since it's within the model's MAE. Prices are parsed via `Math.round(parseFloat(...))` to avoid turning float strings like `"29195.0"` into `"2919500"`.
- **VIN extraction (cobalt_dom):** VINs are extracted from `[data-vin]` attributes, `li.vin` text content (DDC sites), or ipacket.info link hrefs — in that priority order.
- **NHTSA VIN decoder (`tools/vin_decoder.py`):** Free NHTSA vPIC API (`/api/vehicles/decodevin/{VIN}?format=json`) decodes trim, make, model, year, and drive_type from the VIN encoding. Validates that the VIN is exactly 17 chars and matches `[A-HJ-NPR-Z0-9]{17}` before making any HTTP call. Results cached in a module-level dict (treat returned dict as read-only). Called in `sourcing_agent._enrich_trim_from_vin()` as a pre-pass before Pass 1 validation for dealer listings: when a listing has a VIN but its trim is missing or fails `is_trim_allowed`, NHTSA is queried, the result is run through `canonical_trim()`, and accepted if it passes the allowlist. Training data does not store VINs; NHTSA enrichment applies only to active sourcing.
- **`_build_dealer_com_url`** only modifies URLs that contain BOTH `index.htm` AND a CDK path (`/used-inventory/`, `/new-inventory/`, etc.). DealerInspire URLs (`/used-vehicles/`) have neither — they pass through unmodified.
- **Orchestrator timeouts:** 60-minute watchdog; 5-minute cap per training phase; 5-minute cap per scoring phase.
- **Anti-detection:** Browser UA rotates across Chrome 134–136 (macOS + Windows). Viewport randomizes across 5 sizes. Bot-detection check after each CarGurus/Cars.com page load.
- **`analysis_agent.py` has no LLM.** Scores all unscored listings in a single batch using the stacked ensemble.
- **`query_agent.py`** uses LLM (deepseek/deepseek-v4-flash via OpenRouter) for natural-language questions about the listing DB. `max_tokens=500` caps runaway responses.
- **Filter bar is client-side.** All filtering and sorting happens in-memory in Dashboard.tsx.
- **Cars.com model slug** uses `re.sub(r"[^a-z0-9]+", "_", model.lower())` — all non-alphanumeric chars become underscores. Old `str.replace(' ', '_')` left hyphens intact, breaking hyphenated model names.
- **`engine_filter` is source-aware** — bypassed for `carscom` when the model name itself contains the filter keyword (slug already scopes to the right variant). Always applied for CarGurus where the model page may include multiple engine variants.
- **`user_notes`** — `TEXT` column on `listings`; editable via the detail panel textarea and `PATCH /api/listings/:id`. Auto-saves on blur.
- **Stacked model pickle path** — per-model `StackedModel` dataclass saved as `price_model_{slug}.pkl`. `StackedModel.__module__` is forced to `tools.price_model` at definition time to prevent `__main__` pickle namespace issues when training via CLI.
- **Dedup within a run:** `known_urls`, `known_vins`, `known_price_fps`, `known_dealer_fps` fetched once before the source loop, shared across all sources including dealers.
- **LLM token optimization:** All LLM calls use module-level shared OpenAI clients. Insight text pre-processed via `_focus_listing_text()` to a 2000-char focused window. Output caps: insights=150 (raised from 120 to accommodate trim field), trim=30, summary=80, query=500.
- **Postgres runs in Docker** (`docker compose up -d db`). Schema in `db/schema.sql`.
