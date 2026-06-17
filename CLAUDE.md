# CarHunt

A personal used-car deal tracker for the Atlanta metro area. Scrapes CarGurus, Cars.com, CarMax, Carvana, and local dealer websites for target vehicle configurations, scores each listing using a stacked ensemble ML model (GB + KNN + Ridge), and surfaces the best deals in a Next.js dashboard.

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
│       ├── scrapers.py       # Playwright scrapers: CarGurus, Cars.com, CarMax, Carvana
│       ├── dealer_scraper.py # Playwright inventory scraper for dealer websites
│       ├── db.py             # psycopg2 helpers + refresh_self_computed_medians()
│       ├── scoring.py        # score_listing() + detect_drivetrain/powertrain
│       ├── price_model.py    # Stacked ensemble fair-price estimator (GB + KNN + Ridge)
│       ├── base_trims.py     # Vehicle config loader, canonical_trim, LLM trim fallback
│       ├── log_tee.py        # Shared stdout/stderr tee → /tmp/carhunt-refresh.log
│       └── job_tracker.py    # Job progress tracking for dashboard SSE stream
```

---

## Target Vehicles (`agents/cars.json`)

| Make | Model | CarGurus ID | Allowed Trims | Year Constraints |
|---|---|---|---|---|
| Hyundai | Tucson Hybrid | d3141 | SEL Convenience, N Line (≤2025), Limited | 2025–2026 |
| Genesis | GV70 | d3163 | Advanced, Advanced Plus, Sport Prestige (2.5T only) | 2023–2025 |
| Kia | Sportage Hybrid | d3239 | EX, X-Line, SX Prestige | 2023+ |
| Kia | Sportage PHEV | d3244 | EX, EX Premium, X-Line, X-Line Prestige, SX | 2023–2025 |
| Volkswagen | Tiguan | d1104 | SE, SEL R-Line, SE R-Line Black | 2025–2026 |
| Buick | Envision | d2398 | Preferred, Sport Touring, Avenir | 2024–2025 |
| BMW | X1 | d2160 | xDrive28i (always AWD) | 2023–2025 |
| BMW | X3 | d392 | sDrive30i (≤2024), xDrive30i | 2023–2025 |
| Cadillac | XT4 | d2673 | Luxury, Premium Luxury, Sport | 2024–2025 |
| Lexus | NX 350h | d2294 | Premium, Luxury, F SPORT | 2022–2024 |
| Volvo | XC40 | d2624 | Plus, Ultimate (B4 FWD or B5 AWD) | 2023–2025 |
| Volvo | XC60 | d1629 | Plus, Ultimate (B5 gas or Recharge T8 PHEV) | 2023–2025 |

**Search params** (stored in `user_settings` DB table):
- Zip: 30328 · Radius: 100 mi · Max price: $45k · Years: 2025–2026
- `max_mileage` is kept but ignored at the URL level — mileage is validated by rate (see Mileage Validation below)

**CarGurus IDs** are model-specific page identifiers. The new search format uses `cargurus_search_path` (make/model/trim paths encoded as `m{makeId}/d{modelId}/trimName`).

**Make IDs:** Hyundai=m28, Genesis=m203, Kia=m33, VW=m55, Buick=m21, BMW=m3, Cadillac=m22, Lexus=m37, Volvo=m56

**Notes:**
- BMW X1 2023–2025: xDrive28i only (always AWD). sDrive28i and M35i excluded.
- BMW X3 2023–2025: sDrive30i (RWD, ≤2024 only), xDrive30i (AWD). M40i excluded.
- Lexus NX 350h 2022–2024: always AWD. Trim allowlist filters out NX 450h+ variants.
- Volvo XC40: B4 = FWD, B5 = AWD (detected from trim text). No Recharge electric.
- Volvo XC60: includes both B5 gas/MHEV (AWD) and Recharge T8 PHEV (eAWD). Stored as separate model names `"XC60"` and `"XC60 Recharge"` in the DB. `detect_powertrain` uses "recharge" keyword to classify PHEV.
- Genesis GV70: `engine_filter: ["2.5T"]` — shares a CarGurus page with the 3.5T; filter requires "2.5T" in trim. Training trims include Standard/Select for broader depreciation curve.
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
- **Pass 2** — Detail page enrichment: fetches each new listing's page to extract accident count, title status, owner count, and LLM qualitative insights. **Runs for CarGurus, Cars.com, and local dealer sources** (which are enriched optionally to extract packages/CPO from VDP pages). CarMax and Carvana set these fields directly in their scrapers.
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

XC60 gas (B5) and PHEV (Recharge T8) share the same CarGurus model page, stored as separate DB model names: `"XC60"` for gas and `"XC60 Recharge"` for PHEV. The split happens in `sourcing_agent._save_new` and `training_collector._insert` by inspecting the raw trim string before canonicalization. The `allowed_norm` allowlist check must run **before** the split (against the original `"xc60"` name).

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

# Rescrape (slow) — deletes and re-collects from all sources:
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
| `agents/tools/base_trims.py` | Trim normalization logic, LLM fallback, on-disk cache |
| `agents/tools/log_tee.py` | Dashboard log file path, tee behavior |
| `agents/sourcing_agent.py` | Validation rules, fingerprint dedup, scraping flow |
| `agents/tools/dealer_scraper.py` | Dealer scraping strategies, per-site timeouts |
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

## Active Dealer Sites (~60 total)

**Volvo (5):** Dyer & Dyer, North Point, O'Steen Athens, Mall of Georgia, Marietta — all `cobalt` type

**Hyundai (10):** ALM Athens, ALM West, AutoNation MoG, Jim Ellis, Rick Case Atlanta, Rick Case Roswell, South Towne, and others

**Kia (9):** Bulldog Athens, Carriage Alpharetta, Ed Voyles Chamblee, Jim Ellis, Kia Atlanta South, Lou Sobh, Rick Case, Calavan West, Kia MoG

**Volkswagen (6):** Atlanta VW, Hoover Lithia Springs, Jim Ellis, Nalley Alpharetta, Stone Mountain, VW Marietta

**Buick (5):** Carl Black Roswell, Carl Black Kennesaw, Jim Ellis, Rick Hendrick Duluth, Southtowne

**BMW (6):** Athens, Gwinnett, South Atlanta, Global Imports, Nalley Decatur, United Alpharetta

**Lexus (5):** Butler South, Hennessy Atlanta, Hennessy Gwinnett, Nalley Roswell, Nalley Smyrna

**Multi-brand / other (~14):** ALM (6 locations), Amazing Luxury Cars, Nalley Kia, Heyward Allen Toyota, Cadillac dealers (cross-brand discovery)

**Adding a Cobalt dealer:** set `scraper_config = {"type": "cobalt"}`.

**Adding a DealerInspire Algolia dealer:** `scraper_config = {"type": "dealerinspire_algolia", "app_id": "SEWJN80HTN", "api_key": "...", "index": "{slug}_production_inventory"}`.

**CarsCommerce dealers:** BMW of Gwinnett uses `type_slugs = ["Pre-Owned", "Certified Pre-Owned"]` in scraper_config.

---

## Development Notes

- **Status system:** `new` → `active` → `contacted` → `purchased`. `dismissed` = user-removed. Listings are **never** auto-dismissed by score or missed scrapes.
- **No auto-sold:** Scraper never marks listings as sold. User controls this via "Mark Expired".
- **Mileage validation is rate-based:** `mileage / years_old > 14,000` → reject. Hard ceiling (`max_mileage`) set to 999999 in `run()` so no cap at the scraper URL level.
- **Detail-page enrichment** runs concurrently (3 pages at a time) with a 10-minute total session cap. CarGurus and Cars.com require enrichment for history parsing, while dealer sites optionally run enrichment for qualitative insights. LLM insight extraction runs in a background thread per listing (timeout=15s).
- **CarGurus new search format** uses `data-testid="srp-listing-tile"` DOM elements; `_dismiss_cargurus_modal()` handles the "Only show GA listings?" modal.
- **Canonical trim normalization** — 4-pass (prefix → BMW token-reorder → whole-word substring → LLM). Engine prefix regex strips "350h ", "2.0T " etc. before matching. To prevent ingestion failures, `is_trim_allowed` also supports Pass 2 (BMW token-reorder).
- **Title parsing LLM fallback** — `_parse_title` in `dealer_scraper.py` falls back to a cached LLM title parser (`deepseek-v4-flash` via OpenRouter) when standard regex/substring matching fails to resolve a valid make/model. Cache stored in `.title_cache.json`. Uses a strict prompt format (`{"matched": false}`) to prevent force-mapping non-target vehicles. `_llm_model_valid_for_title()` post-validates every LLM result: (1) if the matched model has a powertrain qualifier (Hybrid/PHEV/Recharge), the source title must contain one too; (2) all non-powertrain words in the model name must appear in the title. This prevents cross-variant false positives (e.g. "Elantra Hybrid" title → "Tucson Hybrid" match).
- **Dealer scraping is parallelized** — 5 sites concurrently via `ThreadPoolExecutor(max_workers=5)`. Cobalt sites have 150s timeout (paginate via "Next" button, 2.5s each); others 90s.
- **`_add_used` coerce** uses `_int()` for price/mileage/year — handles decimal strings via `int(float(...))` to avoid stripping the decimal point into a 100x inflated value.
- **dealerinspire_algolia year filter:** Some indexes store year as a string; Algolia numeric range filters silently return 0. Year range removed from Algolia query; filtered by `_add_used` post-scrape with `int()` cast.
- **Orchestrator timeouts:** 60-minute watchdog; 5-minute cap per training phase; 5-minute cap per scoring phase.
- **Anti-detection:** Browser UA rotates across Chrome 134–136 (macOS + Windows). Viewport randomizes across 5 sizes. Bot-detection check after each CarGurus/Cars.com page load.
- **`analysis_agent.py` has no LLM.** Scores all unscored listings in a single batch using the stacked ensemble.
- **`query_agent.py`** uses LLM (deepseek/deepseek-v4-flash via OpenRouter) for natural-language questions about the listing DB. `max_tokens=500` caps runaway responses.
- **Filter bar is client-side.** All filtering and sorting happens in-memory in Dashboard.tsx.
- **Cars.com model slug** uses `re.sub(r"[^a-z0-9]+", "_", model.lower())` — all non-alphanumeric chars become underscores. Old `str.replace(' ', '_')` left hyphens intact, breaking hyphenated model names.
- **`engine_filter` is source-aware** — bypassed for `carscom` when the model name itself contains the filter keyword (slug already scopes to the right variant). Always applied for CarGurus and Carvana/CarMax where the model page may include multiple engine variants.
- **`user_notes`** — `TEXT` column on `listings`; editable via the detail panel textarea and `PATCH /api/listings/:id`. Auto-saves on blur.
- **Stacked model pickle path** — per-model `StackedModel` dataclass saved as `price_model_{slug}.pkl`. `StackedModel.__module__` is forced to `tools.price_model` at definition time to prevent `__main__` pickle namespace issues when training via CLI.
- **Dedup within a run:** `known_urls`, `known_vins`, `known_price_fps`, `known_dealer_fps` fetched once before the source loop, shared across all sources including dealers.
- **LLM token optimization:** All LLM calls use module-level shared OpenAI clients. Insight text pre-processed via `_focus_listing_text()` to a 2000-char focused window. Output caps: insights=150 (raised from 120 to accommodate trim field), trim=30, summary=80, query=500.
- **Postgres runs in Docker** (`docker compose up -d db`). Schema in `db/schema.sql`.
