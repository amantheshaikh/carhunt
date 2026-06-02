# PRD: CarHunt — Weekly Used Car Deal Tracker & Dealer Contact Hub

---

## Context

Aman is shopping for a used Sedan or SUV under ~$25,000 (financing likely), located in and around Atlanta, GA. The market is noisy — listings expire fast, prices vary wildly for the same car, and reaching out to multiple dealers manually is tedious. This tool automates deal discovery, ranks every listing by value-for-money, and streamlines dealer contact from a single web dashboard.

---

## Problem Statement

1. **Discovery is fragmented** — good deals exist across CarGurus, AutoTrader, CarMax, Carvana, and local dealer sites, with no single ranked view.
2. **Pricing is opaque** — it's hard to know if a listing is actually a deal vs. inflated for the market.
3. **Communication overhead** — reaching out to 10+ dealers per week is repetitive and hard to track.
4. **Deals expire** — without weekly automated refresh, good listings disappear unnoticed.

---

## Goals

| Goal | Metric |
|---|---|
| Surface the top 10–20 best-value listings each week | ≥ 10 ranked results per refresh |
| Make "deal quality" transparent and comparable | Each listing has a composite Value Score |
| One-click inquiry copy + dealer URL open | < 1 second from button click to clipboard + tab open |
| Log every dealer contact attempt per listing | Contact log shows timestamp and channel for every outreach |

---

## User

Single user (Aman). No multi-tenancy required. No auth required for v1 — app runs locally only.

---

## Agent Architecture

The system is built as three specialized Claude agents orchestrated by a thin coordinator. Each agent runs as a Python process using the Anthropic SDK with tool use. The Next.js dashboard is purely a UI layer that reads from the database — it does not contain business logic.

```
┌─────────────────────────────────────────────────────┐
│                  Orchestrator                        │
│   Triggered on demand via dashboard "Refresh Now"   │
│   Sequences agents, writes run status to DB         │
└───────────┬─────────────────┬───────────────────────┘
            │                 │                 │
     ┌──────▼──────┐  ┌───────▼──────┐  ┌──────▼──────────┐
     │  Sourcing   │  │  Analysis    │  │  Communication  │
     │   Agent     │  │   Agent      │  │     Agent       │
     │             │  │              │  │                 │
     │ Fetches     │  │ Scores each  │  │ Generates       │
     │ listings    │  │ listing,     │  │ inquiry text,   │
     │ from 4      │  │ ranks by     │  │ logs contact    │
     │ sources     │  │ value score  │  │ attempts        │
     └──────┬──────┘  └───────┬──────┘  └──────┬──────────┘
            │                 │                 │
            └─────────────────▼─────────────────┘
                        PostgreSQL DB
                       (local Docker)
```

### Agent 1 — Sourcing Agent

**Role:** Find every listing that matches the search criteria. Store raw data. Know nothing about scoring or communication.

**Trigger:** On demand — dashboard "Refresh Now" button or direct invocation

**Data sources & fetch method:**

| Source | Method |
|---|---|
| CarGurus | Playwright + stealth — intercept internal JSON API response |
| AutoTrader | Playwright + stealth — intercept internal JSON API response |
| CarMax | Playwright + stealth — intercept internal JSON API response |
| Carvana | Playwright + stealth — intercept internal JSON API response |

All four sites use Akamai or Cloudflare bot protection; plain HTTP calls are blocked. Running from a residential Mac IP at once-weekly volume, Playwright + `playwright-stealth` bypasses fingerprint checks reliably.

**Tools available to this agent:**
| Tool | What it does |
|---|---|
| `search_cargurus(params)` | Calls Browse AI Robot API, returns structured listing results |
| `search_autotrader(params)` | Calls Browse AI Robot API, returns structured listing results |
| `search_carmax(params)` | CarMax public REST API call |
| `search_carvana(params)` | HTTP GET to Carvana JSON search endpoint with spoofed headers |
| `save_listings(listings[])` | Upsert listings into DB; mark unseen ones as new; reset `missed_count` to 0 for re-appearing listings |
| `mark_expired_listings()` | Increment `missed_count` for listings absent this run; set `status = sold` when `missed_count = 2`; set `status = expired` for any listing where `first_seen_at < now() - interval '30 days'` and status not in (`purchased`, `dismissed`) |
| `get_search_settings()` | Read current search parameters from `user_settings` |

**Search parameters:**
- Makes: Toyota, Volkswagen, Nissan, Subaru, Honda, Hyundai, Mazda, Chevrolet, Kia
- Body types: Sedan, SUV/Crossover
- Condition: Used only
- Max price: $27,000
- Min year: 2023
- Max mileage: 30,000
- ZIP: 30301 (Atlanta, GA), radius 100 miles

**Hard filters (agent discards listing if violated):**
- Title status must be Clean — excludes Salvage, Rebuilt, Lemon, Flood, Frame Damage
- Listings with accidents are kept but `accident_count` is stored for the Analysis Agent to penalize

**Output:** Raw listings written to `listings` table with `title_status`, `accident_count`, `is_new` flags.

---

### Agent 2 — Analysis Agent

**Role:** Evaluate every unscored listing and produce a Value Score. Know nothing about scraping or communication.

**Trigger:** Orchestrator, runs immediately after Sourcing Agent completes

**Tools available to this agent:**
| Tool | What it does |
|---|---|
| `get_unscored_listings()` | Fetch listings from DB where no score exists yet |
| `get_market_price(make, model, year, mileage_range)` | Look up median market price (cached in DB; sourced from CarGurus fair market value via Browse AI on first lookup) |
| `get_reliability_score(make, model)` | Lookup from static reliability table; returns 65 for unknown make/model |
| `compute_and_save_score(listing_id, breakdown)` | Upsert score + all component scores to `listing_scores` |
| `flag_paperwork_issue(listing_id, reason)` | Tag listing for dashboard warning badge |

**Value Score formula (0–100):**

| Component | Weight | Logic |
|---|---|---|
| **Price vs. market** | 30% | `(market_median − listing_price) / market_median`, normalized 0–100 |
| **Mileage score** | 25% | `(30,000 − mileage) / 30,000 × 100` |
| **Paperwork score** | 20% | Clean + 0 accidents = 100 · Clean + 1 accident = 40 · Unknown = 30 · Salvage/rebuilt = 0 |
| **Reliability rating** | 15% | From static lookup table; default 65 if not found |
| **Age score** | 10% | Normalized within 2023–current year range |

Financing estimate (stored, not scored): `M = P × [r(1+r)^n] / [(1+r)^n − 1]` at 7.5% APR, 60 months.

**Output:** `listing_scores` rows upserted for every processed listing.

---

### Agent 3 — Communication Agent

**Role:** Generate a pre-filled inquiry message for any listing and log contact attempts. No Gmail API. No OAuth. No email sending.

**Trigger:** User clicks "Contact Dealer" in dashboard → Next.js API route calls agent with `listing_id` → agent returns filled inquiry text → dashboard copies text to clipboard and opens listing URL in new tab.

**Tools available to this agent:**
| Tool | What it does |
|---|---|
| `get_listing_details(listing_id)` | Fetch listing info (year, make, model, price, VIN, dealer phone, listing URL) |
| `get_inquiry_template()` | Read user's current template from `user_settings` |
| `generate_inquiry_text(listing_id)` | Fill template with listing details, return completed string |
| `log_contact_attempt(listing_id, channel)` | Write row to `contact_log`, transition listing status → `contacted` |
| `add_note(listing_id, note_text)` | Save a freeform note on a listing |

**Default inquiry template (editable in settings):**
```
Hi,

I'm interested in your [Year] [Make] [Model] listed at [Price] (VIN: [VIN if available]).

Could you confirm:
1. Is this vehicle still available?
2. What is the lowest OTD (out-the-door) price including all fees?
3. Can you confirm the title is clean with no accidents on record?
4. Are there any known issues or recent service records available?

I'm looking to move quickly. Best way to reach me is email.

Thanks,
Aman
```

**Contact flow (user-facing):**
1. User clicks "Contact Dealer" on any listing card or detail panel
2. Dashboard calls API route → agent generates filled inquiry text
3. Text is copied to clipboard automatically
4. Listing URL opens in a new tab (dealer's contact form)
5. User pastes inquiry into the dealer's contact form and submits
6. Contact attempt logged to DB with timestamp and channel

---

## Listing Lifecycle (Status State Machine)

Every listing has a `status` field that drives what appears in the queue. Only agents and explicit user actions may transition status.

```
                    ┌─────────────┐
                    │     new     │  First seen by Sourcing Agent
                    └──────┬──────┘
                           │ auto (Analysis Agent scores it)
                    ┌──────▼──────┐
              ┌────►│   active    │  Visible in default queue
              │     └──────┬──────┘
              │            │
              │    ┌───────┼────────────────────┐
              │    │       │                    │
              │  user   user clicks        Sourcing Agent
              │  clicks  "Contact          not found in
              │  "Not    Dealer"           2 consecutive
              │  Interested"              weekly runs
              │    │       │                    │
              │  ┌─▼──┐  ┌─▼──────────┐   ┌────▼────┐
              │  │dismissed│contacted  │   │  sold   │
              │  └────┘  └─┬──────────┘   └─────────┘
              │            │ user clicks
              │            │ "Mark Purchased"
              │            │
              │         ┌──▼──────┐
              └─────────│purchased│  Exits queue entirely
                        └─────────┘
```

**Status definitions:**

| Status | Who sets it | Visible in queue? |
|---|---|---|
| `new` | Sourcing Agent on first discovery | Yes — "NEW" badge for 48h |
| `active` | Analysis Agent after scoring | Yes (default view) |
| `dismissed` | User clicks "Not Interested" | No (hidden tab to undo) |
| `contacted` | Communication Agent on first contact attempt | Yes — shown in "Contacted" tab |
| `sold` | Sourcing Agent (auto at missed_count=2) or manual | No — shown in "Sold" tab |
| `expired` | Sourcing Agent (auto at 30 days old) | No — shown in "Expired" tab |
| `purchased` | User clicks "I Bought This" | No — shown in "Purchased" tab |

---

## Queue Management

### How listings leave the active queue

**1. User dismisses a listing**
- Click "Not Interested" on any card
- Status → `dismissed`, removed from default view instantly
- Dismissal is soft — "Dismissed" tab shows all dismissed listings with an "Undo" button
- No data deleted, no DB rows removed

**2. Car sells (automatic detection)**
- Each refresh run, Sourcing Agent collects fresh results
- Any listing whose `external_id` is no longer in the results gets `missed_count` incremented
- If a listing reappears, `missed_count` resets to 0
- `missed_count = 1` → show "May be sold" warning badge (yellow) but keep in queue
- `missed_count = 2` (two consecutive missed runs) → status → `sold`, removed from active queue
- This 2-run buffer avoids false positives when a site temporarily de-lists then relists

**3. Listing age (automatic expiry)**
- Any listing where `first_seen_at < now() - interval '30 days'` is set to `status = expired` during each sourcing run
- Applies to all statuses except `purchased` and `dismissed` (user decisions are preserved)
- Expired listings are shown in a read-only "Expired" tab, not the active queue

**3. User marks as purchased**
- Button on listing detail panel: "I Bought This"
- Status → `purchased`, all active dealings closed

---

### Dashboard queue tabs

```
[ Active (12) ]  [ Contacted (3) ]  [ Dismissed ]  [ Sold ]  [ Expired ]  [ Purchased ]
```

- **Active** — default view, `status IN (new, active)`, sorted by Value Score; snoozed listings filtered with `WHERE (snoozed_until IS NULL OR snoozed_until < now())`
- **Contacted** — `status = contacted`, sorted by most recent contact attempt
- **Dismissed** — grayed out, "Undo" button on each row
- **Sold** — tombstone view showing the car and when it was marked sold
- **Expired** — read-only archive of listings auto-removed after 30 days
- **Purchased** — trophy view — the car you actually bought

### Additional queue actions (per listing card)

| Action | What it does |
|---|---|
| **Not Interested** | status → `dismissed` |
| **Snooze 7 days** | Sets `snoozed_until = now() + 7 days`; listing disappears from Active until then |
| **Contact Dealer** | Copies inquiry text to clipboard + opens listing URL; status → `contacted` |
| **Mark Sold** | Manual override — status → `sold` |
| **I Bought This** | status → `purchased` |

---

## Core Features

### 1. Listing Ingestion (Weekly Cron)
Handled entirely by the **Sourcing Agent** above. Triggered on demand. Increments `missed_count` for absent listings (resets to 0 on reappearance) and triggers sold transition at `missed_count = 2`.

---

### 2. Value Score Algorithm
Handled entirely by the **Analysis Agent** above. Runs immediately after sourcing completes. Can also be triggered for a single listing when a price change is detected on re-scrape.

---

### 3. Dashboard (Web App)

**Tech stack:**
- **Frontend:** Next.js 14 (App Router) + Tailwind CSS + shadcn/ui
- **Backend:** Next.js API routes (thin — reads DB, triggers agents via subprocess)
- **Database:** PostgreSQL via Docker (plain, no full Supabase stack needed)
- **Auth:** None for v1 — local only
- **Agents:** Python 3.11+ with `openai` SDK pointed at OpenRouter, run as local processes

**Dashboard views:**

#### A. Deal Rankings Page (default — Active tab)
- Table/card view of `active` + `new` listings sorted by Value Score (desc)
- Each row shows: rank, photo, make/model/year, body type, mileage, price, est. monthly payment, Value Score badge (color-coded: green ≥70, yellow 40–69, red <40), source logo, days on market, dealer phone, paperwork badge, "May be sold" warning if `missed_count ≥ 1`
- Per-card actions: Not Interested · Snooze · Contact Dealer
- Filters: min score, max price, max mileage, make, body type, title status (clean only toggle)
- Sort: Value Score, Price (asc/desc), Mileage, Year

#### B. Listing Detail Panel
- All listing fields
- Score breakdown (horizontal bar chart showing each component's contribution)
- Comparable listings (same make/model, different trim/year, ranked by score)
- **"Contact Dealer"** button → copies filled inquiry text to clipboard + opens listing URL in new tab
- **Dealer phone number** — prominently displayed with click-to-call link
- **Contact Log** — timestamps and channels of all contact attempts, plus any user notes
- **Add Note** text input — save freeform notes (e.g. "left voicemail", "price negotiated to $22k")
- Full lifecycle action buttons: Snooze · Not Interested · Mark Sold · I Bought This

#### C. Contacted Tab
- All listings with `status = contacted`, sorted by last contact attempt date
- Shows channel used and any notes

#### D. Watchlist
- User can pin/star any listing
- Watchlist shows price change alerts (if price drops between weekly refreshes)
- Watchlist listings get priority re-check during the weekly sourcing run

#### E. Settings Page
- Search preferences (ZIP, radius, max price, max mileage, min year, body types, makes)
- Inquiry template editor (editable text, saved to `user_settings`)
- Manual "Refresh Now" button (triggers Sourcing + Analysis agents)

---

### 4. Dealer Contact

No Gmail API. No OAuth. No email sending from the app.

**"Contact Dealer" flow:**
1. User clicks "Contact Dealer" on any listing
2. Dashboard calls `/api/contact` → Communication Agent generates filled inquiry text
3. Text is auto-copied to clipboard
4. Listing URL opens in a new browser tab (dealer's contact form page)
5. User pastes inquiry text into the form and submits manually
6. Contact attempt logged to `contact_log` with timestamp and channel

**Dealer phone:**
- Displayed prominently on every listing card and detail panel
- Clickable `tel:` link for direct call/text from desktop

**Notes:**
- User can add freeform notes to any listing at any time
- Notes are stored in `contact_log` with a `note` field, associated to the listing

---

### 5. On-Demand Refresh

- Triggered by clicking "Refresh Now" in the dashboard (or running `python orchestrator.py` directly)
- After scrape completes, compares new listings vs. previous run:
  - **New** listings (not seen before) → highlighted with "NEW" badge for 48 hours
  - **Price dropped** listings on watchlist → flagged with price delta
  - **Expired** listings (no longer found after 2 runs) → marked as "Sold"
- Per-source result counts and any errors logged to `refresh_log`

---

## Data Model

```sql
listings (
  id uuid PK,
  source text,           -- 'cargurus' | 'autotrader' | 'carmax' | 'carvana'
  external_id text,      -- source's own ID
  vin text,
  make text, model text, trim text, year int,
  body_type text,        -- 'sedan' | 'suv' | 'crossover'
  mileage int, price int,
  dealer_name text, dealer_phone text, dealer_email text,
  listing_url text, photos_url text[],
  listing_date date, first_seen_at timestamptz, last_seen_at timestamptz,
  status text,           -- 'new' | 'active' | 'dismissed' | 'contacted' | 'sold' | 'expired' | 'purchased'
  missed_count int DEFAULT 0,   -- consecutive weekly runs where listing was absent; resets to 0 on reappearance
  snoozed_until timestamptz,    -- null unless snoozed; active query filters WHERE snoozed_until IS NULL OR snoozed_until < now()
  title_status text,     -- 'clean' | 'salvage' | 'rebuilt' | 'flood' | 'lemon' | 'unknown'
  accident_count int,    -- 0 = clean history, null = unknown
  is_pinned boolean DEFAULT false,
  raw jsonb              -- full scraped payload
)

listing_scores (
  listing_id uuid PK FK → listings,   -- PK enforces one score row per listing; upsert on re-score
  value_score numeric,
  price_vs_market numeric,
  mileage_score numeric,
  age_score numeric,
  reliability_score numeric,
  paperwork_score numeric,
  market_median_price int,
  monthly_payment numeric,
  scored_at timestamptz
)

contact_log (
  id uuid PK,
  listing_id uuid FK → listings,
  contacted_at timestamptz,
  channel text,          -- 'contact_form' | 'phone' | 'other'
  note text              -- freeform note; nullable
)

watchlist (
  id uuid PK,
  listing_id uuid FK → listings,
  added_at timestamptz,
  last_price int         -- for change detection
)

market_price_cache (
  id uuid PK,
  make text, model text, year int,
  mileage_bucket text,   -- e.g. '0-10k' | '10k-20k' | '20k-30k'
  median_price int,
  fetched_at timestamptz
)

user_settings (
  id uuid PK,
  zip_code text,
  radius_miles int,
  max_price int,
  max_mileage int,
  min_year int,
  body_types text[],
  makes text[],
  inquiry_template text
)

refresh_log (
  id uuid PK,
  started_at timestamptz,
  completed_at timestamptz,
  listings_found int,
  listings_new int,
  source_results jsonb,  -- e.g. {"cargurus": 34, "autotrader": 28, "carmax": 12, "carvana": 18}
  source_errors jsonb    -- e.g. {"cargurus": null, "autotrader": "Browse AI timeout"}
)
```

---

## Technical Architecture

```
┌──────────────────────────────────────────────────────┐
│            Next.js Dashboard (localhost:3000)         │
│   Rankings UI · Listing Detail · Contact · Settings  │
│   API routes: read DB, trigger agents via subprocess  │
└────────────────────────┬─────────────────────────────┘
                         │
                  PostgreSQL (Docker)
                         │
        ┌────────────────┼─────────────────┐
        │                │                 │
 ┌──────▼──────┐  ┌──────▼──────┐  ┌──────▼──────────┐
 │  Sourcing   │  │  Analysis   │  │  Communication  │
 │   Agent     │  │   Agent     │  │     Agent       │
 │  (Python)   │  │  (Python)   │  │   (Python)      │
 │             │  │             │  │                 │
 │ OpenRouter  │  │ OpenRouter  │  │  OpenRouter     │
 │ Playwright  │  │ + DB reads  │  │  + DB writes    │
 │ + stealth   │  │             │  │                 │
 │ (4 sources) │  │             │  │                 │
 └─────────────┘  └─────────────┘  └─────────────────┘
        │                │                 │
        └────────────────▼─────────────────┘
                  OpenRouter API
                  google/gemma-4-31b-it:free (default)
                  swap to deepseek/deepseek-v4-flash if needed
```

**Local-only setup (no hosting required):**
- Next.js dashboard → `npm run dev` on localhost:3000
- PostgreSQL → `docker run postgres` (plain, no full Supabase stack)
- Agents → `python agents/orchestrator.py` triggered by dashboard button or direct invocation
- All data stays on your machine — only external calls are to Claude API and Browse AI API

**Python project structure:**
```
agents/
  orchestrator.py         # sequences sourcing → analysis, manages run log
  sourcing_agent.py       # Browse AI API + CarMax REST + Carvana JSON + optional Playwright
  analysis_agent.py       # scoring tools
  communication_agent.py  # inquiry text generation + contact log writes
  tools/
    scrapers.py           # Playwright + playwright-stealth for all 4 sources
    db.py                 # PostgreSQL client + DB helpers
    scoring.py            # Value Score computation
    reliability.py        # Static reliability lookup table
```

---

## Reliability Data (Static Table — Top Models)

Pre-populated lookup. Any make/model not listed defaults to **65**.

| Make/Model | Reliability Score |
|---|---|
| Toyota Camry | 92 |
| Toyota RAV4 | 90 |
| Toyota Corolla | 91 |
| Honda Accord | 88 |
| Honda CR-V | 87 |
| Honda Civic | 89 |
| Mazda CX-5 | 88 |
| Mazda3 | 87 |
| Subaru Outback | 78 |
| Subaru Forester | 77 |
| Hyundai Sonata | 74 |
| Hyundai Tucson | 73 |
| Hyundai Elantra | 75 |
| Kia Sportage | 74 |
| Kia Forte | 76 |
| Kia Telluride | 80 |
| Kia Sorento | 75 |
| Nissan Altima | 70 |
| Nissan Rogue | 68 |
| Volkswagen Jetta | 69 |
| Volkswagen Tiguan | 67 |
| Volkswagen Passat | 66 |
| Chevrolet Equinox | 63 |
| Chevrolet Malibu | 61 |

---

## Out of Scope (v1)

- Mobile app
- Multi-user / sharing
- Gmail integration (outbound or inbound)
- VIN history report integration (Carfax/AutoCheck) — API cost
- Automated negotiation or price offers
- Test drive scheduling
- Financing calculator with real lender rates
- Facebook Marketplace (requires personal account scraping, ToS risk)
- Local dealer scraping (Playwright fallback — optional P6 add-on)

---

## Milestones

| Phase | Deliverable | Est. Effort |
|---|---|---|
| **P0 — Foundation** | PostgreSQL schema (Docker), Next.js scaffold, Anthropic SDK setup | 0.5 days |
| **P1 — Sourcing Agent** | Browse AI robots for CarGurus + AutoTrader, CarMax REST, Carvana JSON endpoint, listings saved to DB | 2 days |
| **P2 — Analysis Agent** | Claude agent + scoring tools, Value Score written to DB, market price cache | 1 day |
| **P3 — Orchestrator** | Orchestrator sequences P1→P2, on-demand trigger from dashboard, run log with per-source tracking | 0.5 days |
| **P4 — Dashboard** | Rankings page, filters, score breakdown, listing detail panel, contact log | 2 days |
| **P5 — Communication Agent** | Inquiry text generation, clipboard copy + URL open flow, contact log, notes | 1 day |
| **P6 — Polish** | Watchlist, price alerts, settings page, local dealer Playwright scraper (optional) | 1.5 days |

**Total estimate: ~8.5 days of focused work**

---

## Success Criteria

- [ ] On demand refresh surfaces 10+ ranked used sedan/SUV listings in the dashboard
- [ ] Value Score correctly identifies CarGurus-labeled "Great Deal" listings in top quartile
- [ ] "Contact Dealer" copies filled inquiry text to clipboard and opens listing URL in < 1 second
- [ ] Contact log shows timestamp and channel for every outreach attempt
- [ ] Dashboard loads under 2 seconds with 200+ listings in DB

---

## Known Gaps & Open Questions

1. **Browse AI robot setup is manual** — robots must be recorded via Chrome extension before the Sourcing Agent can call them. This is a one-time setup step, not automatable in code.
2. **Carvana JSON endpoint is unofficial** — no SLA, could change without notice. If it breaks, fall back to a Browse AI Robot #3 (would require upgrading to a paid plan) or drop Carvana.
3. **Market price caching strategy** — `get_market_price` fetches from CarGurus fair market value via Browse AI on first lookup and caches in `market_price_cache`. Cache TTL not yet defined — suggest 7 days (refresh weekly alongside listings).
4. **Photos** — listing photo URLs from external sites will expire as listings age. For v1, store URLs as-is and accept that photos may 404 on old listings.
