export type ListingStatus =
  | "new"
  | "active"
  | "dismissed";

export interface Listing {
  id: string;
  source: string;
  external_id: string;
  vin: string | null;
  make: string;
  model: string;
  trim: string | null;
  year: number;
  body_type: string | null;
  mileage: number | null;
  price: number;
  dealer_name: string | null;
  dealer_phone: string | null;
  dealer_email: string | null; // stored but not displayed
  listing_url: string;
  photos_url: string[] | null;
  first_seen_at: string;
  last_seen_at: string;
  status: ListingStatus;
  snoozed_until: string | null;
  title_status: string | null;
  accident_count: number | null;
  drivetrain: "fwd" | "awd" | null;
  is_pinned: boolean;
  condition: 'new' | 'used';
  insights: {
    cpo: boolean;
    packages: string[];
    price_drop: number | null;
    owner_notes: string | null;
  } | null;
  // joined from listing_scores (Stacked Ensemble model)
  value_score: number | null;
  fair_price: number | null;
  market_price_source: string | null;
  shap_values: {
    base_value: number;
    predicted: number;
    features: { label: string; delta: number; raw: number }[];
    gb_pred?: number;
    knn_pred?: number | null;
    ridge_pred?: number | null;
    meta_weights?: number[];
  } | null;
}


export interface RefreshLog {
  id: string;
  started_at: string;
  completed_at: string | null;
  listings_found: number | null;
  listings_new: number | null;
  source_results: Record<string, number> | null;
  source_errors: Record<string, string | null> | null;
  summary: string | null;
}
