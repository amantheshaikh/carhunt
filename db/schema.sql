-- CarHunt database schema

CREATE TABLE IF NOT EXISTS listings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source TEXT NOT NULL,                  -- 'cargurus' | 'autotrader' | 'carmax' | 'carvana'
  external_id TEXT NOT NULL,             -- source's own listing ID
  vin TEXT,
  make TEXT NOT NULL,
  model TEXT NOT NULL,
  trim TEXT,
  year INT NOT NULL,
  body_type TEXT,                        -- 'sedan' | 'suv' | 'crossover'
  drivetrain TEXT,
  mileage INT,
  price INT NOT NULL,
  dealer_name TEXT,
  dealer_phone TEXT,
  dealer_email TEXT,
  listing_url TEXT NOT NULL,
  photos_url TEXT[],
  listing_date DATE,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  status TEXT NOT NULL DEFAULT 'new',    -- 'new' | 'active' | 'dismissed' | 'contacted' | 'sold' | 'expired' | 'purchased'
  missed_count INT NOT NULL DEFAULT 0,
  snoozed_until TIMESTAMPTZ,
  title_status TEXT,                     -- 'clean' | 'salvage' | 'rebuilt' | 'flood' | 'lemon' | 'unknown'
  accident_count INT,
  is_pinned BOOLEAN NOT NULL DEFAULT false,
  condition TEXT NOT NULL DEFAULT 'used',    -- 'new' | 'used'
  raw JSONB,
  UNIQUE(source, external_id)
);

CREATE TABLE IF NOT EXISTS listing_scores (
  listing_id UUID PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
  value_score NUMERIC NOT NULL,
  price_vs_market NUMERIC,
  mileage_score NUMERIC,
  age_score NUMERIC,
  reliability_score NUMERIC,
  paperwork_score NUMERIC,
  fair_price          INT,                   -- ML-predicted fair price
  market_price_source TEXT,                  -- 'ml_model'
  monthly_payment NUMERIC,
  shap_values JSONB,
  scored_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS watchlist (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  listing_id UUID NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
  added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_price INT,
  UNIQUE(listing_id)
);

CREATE TABLE IF NOT EXISTS market_price_cache (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  make TEXT NOT NULL,
  model TEXT NOT NULL,
  year INT NOT NULL,
  mileage_bucket TEXT NOT NULL,          -- '0-10k' | '10k-20k' | '20k-30k'
  median_price INT NOT NULL,
  source TEXT NOT NULL DEFAULT 'scraped', -- 'scraped' | 'kbb'
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(make, model, year, mileage_bucket)
);

CREATE TABLE IF NOT EXISTS user_settings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  zip_code TEXT NOT NULL DEFAULT '30301',
  radius_miles INT NOT NULL DEFAULT 100,
  max_price INT NOT NULL DEFAULT 27000,
  max_mileage INT NOT NULL DEFAULT 30000,
  min_year INT NOT NULL DEFAULT 2023,
  body_types TEXT[] NOT NULL DEFAULT ARRAY['sedan', 'suv', 'crossover'],
  makes TEXT[] NOT NULL DEFAULT ARRAY['Toyota', 'Volkswagen', 'Nissan', 'Subaru', 'Honda', 'Hyundai', 'Mazda', 'Chevrolet', 'Kia'],
  inquiry_template TEXT NOT NULL DEFAULT 'Hi,

I''m interested in your {year} {make} {model} listed at {price} (VIN: {vin}).

Could you confirm:
1. Is this vehicle still available?
2. What is the lowest OTD (out-the-door) price including all fees?
3. Can you confirm the title is clean with no accidents on record?
4. Are there any known issues or recent service records available?

I''m looking to move quickly. Best way to reach me is email.

Thanks,
Aman'
);

CREATE TABLE IF NOT EXISTS dealer_sites (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  brand TEXT NOT NULL,
  name TEXT NOT NULL,
  website_url TEXT NOT NULL,
  inventory_url TEXT,
  phone TEXT,
  city TEXT,
  distance_miles NUMERIC,
  is_active BOOLEAN NOT NULL DEFAULT true,
  last_scraped_at TIMESTAMPTZ,
  added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(website_url)
);

CREATE TABLE IF NOT EXISTS refresh_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ,
  listings_found INT,
  listings_new INT,
  source_results JSONB,
  source_errors JSONB
);


-- Insert default settings row if not exists
INSERT INTO user_settings (id) VALUES (gen_random_uuid())
ON CONFLICT DO NOTHING;

-- Migrations for existing databases
ALTER TABLE listings ADD COLUMN IF NOT EXISTS condition TEXT NOT NULL DEFAULT 'used';
ALTER TABLE listing_scores ADD COLUMN IF NOT EXISTS market_price_source TEXT;
ALTER TABLE listing_scores ADD COLUMN IF NOT EXISTS shap_values JSONB;
CREATE INDEX IF NOT EXISTS idx_listings_condition ON listings(condition);
ALTER TABLE price_training_data ADD COLUMN IF NOT EXISTS cpo BOOLEAN DEFAULT false;

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status);
CREATE INDEX IF NOT EXISTS idx_listings_source ON listings(source);
CREATE INDEX IF NOT EXISTS idx_listings_first_seen ON listings(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_listings_snoozed ON listings(snoozed_until) WHERE snoozed_until IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_watchlist_listing ON watchlist(listing_id);
