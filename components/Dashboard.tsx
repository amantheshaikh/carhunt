"use client";

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import type { Listing, RefreshLog } from "@/lib/types";
import type { JobStatus } from "@/app/api/job-status/route";
import { getDrivetrainTag } from "@/lib/vehicle-tags";
import ListingCard from "./ListingCard";
import ListingDetail from "./ListingDetail";
import QueryChat from "./QueryChat";
import TrainingDataTab from "./TrainingDataTab";

// ─── Filter + sort types ──────────────────────────────────────────────────────

type SortKey = "score" | "price_asc" | "price_desc" | "mileage_asc" | "below_market" | "newest";

interface Filters {
  models: string[];          // empty = all
  years: number[];           // empty = all
  drivetrain: "" | "awd" | "fwd";
  maxPrice: number | "";
  maxMileage: number | "";
  minDealScore: number | "";  // minimum value_score threshold mapped from deal category
}

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "score",        label: "Best Match" },
  { value: "below_market", label: "Below Market %" },
  { value: "price_asc",    label: "Price: Low → High" },
  { value: "price_desc",   label: "Price: High → Low" },
  { value: "mileage_asc",  label: "Mileage: Low → High" },
  { value: "newest",       label: "Newest Listed" },
];

const DEFAULT_FILTERS: Filters = {
  models: [], years: [], drivetrain: "", maxPrice: "", maxMileage: "", minDealScore: "",
};


function applyFiltersAndSort(listings: Listing[], filters: Filters, sort: SortKey): Listing[] {
  let out = listings.filter((l) => {
    if (filters.models.length && !filters.models.includes(l.model)) return false;
    if (filters.years.length && !filters.years.includes(l.year)) return false;
    if (filters.drivetrain && getDrivetrainTag(l.trim, l.model, l.drivetrain) !== filters.drivetrain) return false;
    if (filters.maxPrice !== "" && l.price > filters.maxPrice) return false;
    if (filters.maxMileage !== "" && (l.mileage ?? 0) > filters.maxMileage) return false;
    if (filters.minDealScore !== "" && (l.value_score ?? 0) < filters.minDealScore) return false;
    return true;
  });

  out = [...out].sort((a, b) => {
    switch (sort) {
      case "price_asc":    return (a.price ?? 0) - (b.price ?? 0);
      case "price_desc":   return (b.price ?? 0) - (a.price ?? 0);
      case "mileage_asc":  return (a.mileage ?? 0) - (b.mileage ?? 0);
      case "newest":       return new Date(b.first_seen_at).getTime() - new Date(a.first_seen_at).getTime();
      case "below_market": {
        const discountA = a.fair_price ? (a.fair_price - a.price) / a.fair_price : -1;
        const discountB = b.fair_price ? (b.fair_price - b.price) / b.fair_price : -1;
        return discountB - discountA;
      }
      default: return (b.value_score ?? 0) - (a.value_score ?? 0);
    }
  });

  return out;
}

// ─── Shared filter chip ───────────────────────────────────────────────────────

function FilterChip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`px-2.5 py-1 text-xs rounded-full border transition-colors ${
        active
          ? "bg-blue-700 border-blue-600 text-white"
          : "bg-zinc-800 border-zinc-700 text-zinc-300 hover:bg-zinc-700"
      }`}
    >
      {children}
    </button>
  );
}

// ─── Filter bar component ─────────────────────────────────────────────────────

function FilterBar({
  listings, filters, sort, onFilters, onSort,
}: {
  listings: Listing[];
  filters: Filters;
  sort: SortKey;
  onFilters: (f: Filters) => void;
  onSort: (s: SortKey) => void;
}) {
  const [open, setOpen] = useState(false);

  const models = useMemo(() => {
    const seen = new Set<string>();
    return listings.map((l) => l.model).filter((m) => m && !seen.has(m) && seen.add(m)).sort();
  }, [listings]);

  const activeCount = useMemo(() => {
    let n = 0;
    if (filters.models.length) n++;
    if (filters.years.length) n++;
    if (filters.drivetrain) n++;
    if (filters.maxPrice !== "") n++;
    if (filters.maxMileage !== "") n++;
    if (filters.minDealScore !== "") n++;
    return n;
  }, [filters]);

  const toggleModel = (m: string) =>
    onFilters({ ...filters, models: filters.models.includes(m) ? filters.models.filter((x) => x !== m) : [...filters.models, m] });

  const toggleYear = (y: number) =>
    onFilters({ ...filters, years: filters.years.includes(y) ? filters.years.filter((x) => x !== y) : [...filters.years, y] });

  const years = useMemo(() => {
    const seen = new Set<number>();
    return listings.map((l) => l.year).filter((y) => y && !seen.has(y) && seen.add(y)).sort((a, b) => b - a);
  }, [listings]);

  return (
    <div className="border-b border-zinc-800 bg-zinc-950">
      {/* Toolbar row */}
      <div className="flex items-center gap-3 px-4 py-2">
        <select
          value={sort}
          onChange={(e) => onSort(e.target.value as SortKey)}
          className="text-xs bg-zinc-800 border border-zinc-700 text-zinc-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-blue-500"
        >
          {SORT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>

        <button
          onClick={() => setOpen((v) => !v)}
          className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border transition-colors ${
            open || activeCount > 0
              ? "bg-blue-900 border-blue-700 text-blue-200"
              : "bg-zinc-800 border-zinc-700 text-zinc-300 hover:bg-zinc-700"
          }`}
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M3 4h18M7 12h10M11 20h2" />
          </svg>
          Filters
          {activeCount > 0 && (
            <span className="ml-0.5 px-1.5 py-0.5 text-xs bg-blue-600 text-white rounded-full leading-none">{activeCount}</span>
          )}
        </button>

        {activeCount > 0 && (
          <button onClick={() => onFilters(DEFAULT_FILTERS)} className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors">
            Clear all
          </button>
        )}
      </div>

      {/* Expanded filters panel */}
      {open && (
        <div className="px-4 pb-3 space-y-3">
          {/* Model + Year chips row */}
          <div className="flex flex-wrap gap-4">
            <div>
              <p className="text-xs text-zinc-500 mb-1.5 font-medium">Model</p>
              <div className="flex flex-wrap gap-1.5">
                {models.map((m) => (
                  <FilterChip key={m} active={filters.models.includes(m)} onClick={() => toggleModel(m)}>
                    {m}
                  </FilterChip>
              ))}
              </div>
            </div>

            <div>
              <p className="text-xs text-zinc-500 mb-1.5 font-medium">Year</p>
              <div className="flex flex-wrap gap-1.5">
                {years.map((y) => (
                  <FilterChip key={y} active={filters.years.includes(y)} onClick={() => toggleYear(y)}>
                    {y}
                  </FilterChip>
                ))}
              </div>
            </div>
          </div>

          {/* Drivetrain + Powertrain + Score row */}
          <div className="flex flex-wrap gap-4">
            <div>
              <p className="text-xs text-zinc-500 mb-1.5 font-medium">Drivetrain</p>
              <div className="flex gap-1.5">
                {[["", "All"], ["awd", "AWD"], ["fwd", "FWD"]].map(([v, label]) => (
                  <FilterChip key={v} active={filters.drivetrain === v} onClick={() => onFilters({ ...filters, drivetrain: v as Filters["drivetrain"] })}>
                    {label}
                  </FilterChip>
                ))}
              </div>
            </div>

            <div>
              <p className="text-xs text-zinc-500 mb-1.5 font-medium">Deal quality</p>
              <div className="flex gap-1.5">
                {([["", "Any"], [45, "Fair Price+"], [58, "Good Deal+"], [72, "Great Deal+"], [88, "Exceptional"]] as [number|"", string][]).map(([v, label]) => (
                  <FilterChip key={String(v)} active={filters.minDealScore === v} onClick={() => onFilters({ ...filters, minDealScore: v as Filters["minDealScore"] })}>
                    {label}
                  </FilterChip>
                ))}
              </div>
            </div>
          </div>

          {/* Price + Mileage inputs */}
          <div className="flex flex-wrap gap-4">
            <div>
              <p className="text-xs text-zinc-500 mb-1.5 font-medium">Max price</p>
              <div className="relative">
                <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-400 text-xs pointer-events-none">$</span>
                <input
                  type="number"
                  min={0}
                  step={500}
                  placeholder="Any"
                  value={filters.maxPrice === "" ? "" : filters.maxPrice}
                  onChange={(e) => onFilters({ ...filters, maxPrice: e.target.value === "" ? "" : Number(e.target.value) })}
                  className="pl-6 pr-3 py-1.5 text-xs bg-zinc-800 border border-zinc-700 text-zinc-200 rounded-lg w-32 focus:outline-none focus:border-blue-500 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                />
              </div>
            </div>

            <div>
              <p className="text-xs text-zinc-500 mb-1.5 font-medium">Max mileage</p>
              <div className="relative">
                <input
                  type="number"
                  min={0}
                  step={1000}
                  placeholder="Any"
                  value={filters.maxMileage === "" ? "" : filters.maxMileage}
                  onChange={(e) => onFilters({ ...filters, maxMileage: e.target.value === "" ? "" : Number(e.target.value) })}
                  className="pl-3 pr-8 py-1.5 text-xs bg-zinc-800 border border-zinc-700 text-zinc-200 rounded-lg w-32 focus:outline-none focus:border-blue-500 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                />
                <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 text-xs pointer-events-none">mi</span>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const TABS: { key: string; label: string }[] = [
  { key: "active", label: "Active" },
  { key: "dismissed", label: "Dismissed" },
  { key: "training", label: "Training Data" },
];

const REFRESH_TIMEOUT_MS = 30 * 60 * 1000; // 30 minutes — matches orchestrator watchdog

function RefreshBanner({ log, onDone }: { log: RefreshLog | null; onDone: () => void }) {
  const isTimedOut = !!log && !log.completed_at &&
    (Date.now() - new Date(log.started_at).getTime()) > REFRESH_TIMEOUT_MS;
  const isRunning = !!log && !log.completed_at && !isTimedOut;
  const justFinished = !!log && !!log.completed_at;

  useEffect(() => {
    if (justFinished) {
      const t = setTimeout(onDone, 5000);
      return () => clearTimeout(t);
    }
  }, [justFinished, onDone]);

  // Auto-dismiss stalled banner after 30s — don't make user hunt for a button
  useEffect(() => {
    if (isTimedOut) {
      const t = setTimeout(onDone, 30_000);
      return () => clearTimeout(t);
    }
  }, [isTimedOut, onDone]);

  if (!log) return null;

  if (isTimedOut) {
    return (
      <div className="flex items-center justify-between px-6 py-2.5 bg-red-950 border-b border-red-900 text-red-200 text-sm">
        <span>Refresh stalled — a scraper likely hung. You can try again.</span>
        <button onClick={onDone} className="ml-4 text-red-400 hover:text-red-200">Dismiss</button>
      </div>
    );
  }

  if (isRunning) {
    return (
      <div className="flex items-center gap-3 px-6 py-2.5 bg-blue-950 border-b border-blue-900 text-blue-200 text-sm">
        <span className="inline-block w-3 h-3 rounded-full bg-blue-400 animate-pulse flex-shrink-0" />
        <span>Refresh in progress — scraping CarGurus, Cars.com, and dealer sites. This takes 10–20 minutes.</span>
      </div>
    );
  }

  const found = log.source_results
    ? Object.values(log.source_results as Record<string, number>).reduce((a, b) => a + b, 0)
    : log.listings_found ?? 0;
  const errors = log.source_errors
    ? Object.entries(log.source_errors as Record<string, string | null>)
        .filter(([, v]) => v)
        .map(([k]) => k)
    : [];

  return (
    <div className={`px-6 py-2.5 border-b text-sm ${
      errors.length > 0
        ? "bg-yellow-950 border-yellow-900 text-yellow-200"
        : "bg-green-950 border-green-900 text-green-200"
    }`}>
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex-shrink-0">{errors.length > 0 ? "⚠" : "✓"}</span>
        <div className="flex flex-col gap-1">
          <span>
            Refresh complete — {found} listings found, {log.listings_new ?? 0} scored.
            {errors.length > 0 && ` Errors on: ${errors.join(", ")}.`}
          </span>
          {log.summary && (
            <span className={`text-xs ${errors.length > 0 ? "text-yellow-300" : "text-green-300"}`}>
              {log.summary}
            </span>
          )}
        </div>
        <button onClick={onDone} className={`ml-auto text-xs flex-shrink-0 ${errors.length > 0 ? "text-yellow-600 hover:text-yellow-300" : "text-green-600 hover:text-green-300"}`}>Dismiss</button>
      </div>
    </div>
  );
}

const JOB_LABELS: Record<string, string> = { refresh: "Refresh", retrain: "Retrain" };

function JobBanner({ job, onDismiss }: { job: JobStatus | null; onDismiss: () => void }) {
  const isRunning = !!job && job.status === "running";
  const justFinished = !!job && job.status === "complete";
  const isError = !!job && job.status === "error";
  const [showLog, setShowLog] = useState(true);
  const [logLines, setLogLines] = useState<string[]>([]);
  const logRef = useRef<HTMLDivElement>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (justFinished || isError) {
      const t = setTimeout(onDismiss, 8000);
      return () => clearTimeout(t);
    }
  }, [justFinished, isError, onDismiss]);

  // Open SSE stream when job starts, close when it stops.
  useEffect(() => {
    if (!isRunning) {
      esRef.current?.close();
      esRef.current = null;
      return;
    }
    if (esRef.current) return;
    setLogLines([]);
    const es = new EventSource(new URL("/api/logs/stream", window.location.origin).toString());
    esRef.current = es;
    es.onmessage = (e) => {
      let line: string;
      try {
        line = JSON.parse(e.data) as string;
      } catch {
        return; // skip malformed/truncated SSE chunk rather than crash the stream
      }
      setLogLines((prev) => {
        const next = [...prev, line];
        return next.length > 500 ? next.slice(-500) : next;
      });
    };
    return () => { es.close(); esRef.current = null; };
  }, [isRunning]);

  // Auto-scroll to bottom when new lines arrive.
  useEffect(() => {
    if (showLog && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logLines, showLog]);

  if (!job) return null;

  const typeLabel = JOB_LABELS[job.job_type] ?? job.job_type;
  const pct = job.stage_current && job.stage_total
    ? Math.round((job.stage_current / job.stage_total) * 100)
    : null;

  if (isError) {
    return (
      <div className="flex items-center justify-between px-6 py-2.5 bg-red-950 border-b border-red-900 text-red-200 text-sm">
        <span>{typeLabel} failed{job.stage_label ? ` during: ${job.stage_label}` : ""}.</span>
        <button onClick={onDismiss} className="ml-4 text-red-400 hover:text-red-200 text-xs">Dismiss</button>
      </div>
    );
  }

  if (justFinished) {
    return (
      <div className="flex items-center gap-3 px-6 py-2.5 bg-green-950 border-b border-green-900 text-green-200 text-sm">
        <span>✓</span>
        <span>{typeLabel} complete{job.rows_added ? ` — ${job.rows_added} rows collected` : ""}.</span>
        <button onClick={onDismiss} className="ml-auto text-green-600 hover:text-green-300 text-xs">Dismiss</button>
      </div>
    );
  }

  if (isRunning) {
    return (
      <div className="border-b border-violet-900 bg-violet-950">
        <div className="flex items-center gap-3 px-6 py-2 text-violet-200 text-sm">
          <span className="inline-block w-2.5 h-2.5 rounded-full bg-violet-400 animate-pulse flex-shrink-0" />
          <span className="font-medium">{typeLabel} in progress</span>
          {job.stage_current && job.stage_total && (
            <span className="text-violet-400 text-xs">
              stage {job.stage_current}/{job.stage_total}
            </span>
          )}
          {job.stage_label && (
            <span className="text-violet-300 text-xs truncate">— {job.stage_label}</span>
          )}
          {job.rows_added > 0 && (
            <span className="text-violet-400 text-xs">{job.rows_added} rows</span>
          )}
          <button
            onClick={() => setShowLog((v) => !v)}
            className="ml-auto text-violet-400 hover:text-violet-200 text-xs flex-shrink-0"
          >
            {showLog ? "Hide logs ▲" : "Show logs ▼"}
          </button>
        </div>
        {pct !== null && (
          <div className="h-0.5 bg-violet-900">
            <div
              className="h-full bg-violet-500 transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
        )}
        {showLog && (
          <div
            ref={logRef}
            className="max-h-48 overflow-y-auto bg-black/60 px-4 py-2 font-mono text-xs leading-relaxed"
          >
            {logLines.length === 0 ? (
              <span className="text-zinc-600">Waiting for output...</span>
            ) : (
              logLines.map((line, i) => (
                <div key={i} className={
                  line.includes("ERROR") || line.includes("TIMEOUT") ? "text-red-400" :
                  line.includes("new rows") ? "text-green-400" :
                  line.includes("starting...") ? "text-zinc-400" :
                  "text-zinc-300"
                }>
                  {line}
                </div>
              ))
            )}
          </div>
        )}
      </div>
    );
  }

  return null;
}

export default function Dashboard() {
  const [tab, setTab] = useState("active");
  const [listings, setListings] = useState<Listing[]>([]);
  const [selected, setSelected] = useState<Listing | null>(null);
  const [loading, setLoading] = useState(true);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [activeLog, setActiveLog] = useState<RefreshLog | null>(null);
  const [lastRefreshedAt, setLastRefreshedAt] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [sort, setSort] = useState<SortKey>("score");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const refreshStartedAt = useRef<Date | null>(null);
  const [activeJob, setActiveJob] = useState<JobStatus | null>(null);
  const jobPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const displayedListings = useMemo(
    () => applyFiltersAndSort(listings, filters, sort),
    [listings, filters, sort]
  );

  const fetchListings = useCallback(async (status: string) => {
    if (status === "training") return;  // training tab handles its own data
    setLoading(true);
    const res = await fetch(`/api/listings?status=${status}`);
    const data = await res.json();
    setListings(data);
    setLoading(false);
  }, []);

  const fetchCounts = useCallback(async () => {
    const listingTabKeys = TABS.filter((t) => t.key !== "training").map((t) => t.key);
    const results = await Promise.all(
      listingTabKeys.map((k) => fetch(`/api/listings?status=${k}`).then((r) => r.json()))
    );
    const newCounts: Record<string, number> = {};
    listingTabKeys.forEach((k, i) => { newCounts[k] = results[i].length; });
    setCounts(newCounts);
  }, []);

  const checkRefreshLog = useCallback(async () => {
    const res = await fetch("/api/refresh-log");
    const logs: RefreshLog[] = await res.json();
    const latest = logs[0] ?? null;

    // If we triggered a refresh, ignore log entries that predate our click
    const isOurs = latest && refreshStartedAt.current
      ? new Date(latest.started_at) >= refreshStartedAt.current
      : !!latest;

    if (!isOurs) {
      // Orchestrator hasn't written its log entry yet — keep polling
      // But if a completed entry exists and we've been waiting >2 min, accept it
      const waitedMs = refreshStartedAt.current
        ? Date.now() - refreshStartedAt.current.getTime()
        : 0;
      if (latest?.completed_at && waitedMs > 120_000) {
        setActiveLog(latest);
        setLastRefreshedAt(latest.completed_at);
        return true;
      }
      return false;
    }

    setActiveLog(latest);

    if (latest && !latest.completed_at) {
      return false;
    }
    if (latest?.completed_at) setLastRefreshedAt(latest.completed_at);
    return true;
  }, []);

  const startPolling = useCallback(() => {
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      const done = await checkRefreshLog();
      if (done) {
        clearInterval(pollRef.current!);
        pollRef.current = null;
        fetchListings(tab);
        fetchCounts();
      }
    }, 3000);
  }, [checkRefreshLog, fetchListings, fetchCounts, tab]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // On mount: load the last completed run timestamp, then check if a refresh is running
  useEffect(() => {
    fetch("/api/refresh-log")
      .then((r) => r.json())
      .then((logs: RefreshLog[]) => {
        const lastDone = logs.find((l) => l.completed_at);
        if (lastDone?.completed_at) setLastRefreshedAt(lastDone.completed_at);
      });
    checkRefreshLog().then((done) => {
      if (!done) startPolling();
    });
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    fetchListings(tab);
  }, [tab, fetchListings]);

  useEffect(() => {
    fetchCounts();
  }, [fetchCounts]);

  useEffect(() => () => stopPolling(), [stopPolling]);

  // Poll /api/job-status every 5s — shows retrain/refresh progress from background_jobs table
  useEffect(() => {
    const poll = async () => {
      const res = await fetch("/api/job-status");
      const job: JobStatus | null = await res.json();
      if (job && (job.status === "running" || (job.status !== "running" && activeJob?.status === "running"))) {
        setActiveJob(job);
      } else if (!job) {
        setActiveJob(null);
      }
    };
    poll();
    jobPollRef.current = setInterval(poll, 5000);
    return () => { if (jobPollRef.current) clearInterval(jobPollRef.current); };
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  const handleRefresh = async () => {
    refreshStartedAt.current = new Date();
    await fetch("/api/refresh", { method: "POST" });
    // Show banner immediately before the orchestrator writes its log entry
    setActiveLog({ id: "", started_at: refreshStartedAt.current.toISOString(), completed_at: null, listings_found: null, listings_new: null, source_results: null, source_errors: null, summary: null });
    startPolling();
  };

  const handleAction = async (id: string, action: string, value?: unknown) => {
    const statusMap: Record<string, string> = {
      dismiss: "dismissed",
      undo: "active",
    };

    if (action === "snooze") {
      const until = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();
      await fetch(`/api/listings/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ snoozed_until: until }),
      });
    } else if (action === "pin") {
      await fetch(`/api/listings/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_pinned: value }),
      });
    } else if (statusMap[action]) {
      await fetch(`/api/listings/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: statusMap[action] }),
      });
    }

    fetchListings(tab);
    fetchCounts();
    if (selected?.id === id) {
      if (action !== "edited") {
        setSelected(null);
      } else {
        try {
          const res = await fetch(`/api/listings/${id}`);
          if (res.ok) {
            const updated = await res.json();
            setSelected(updated);
          }
        } catch (e) {
          console.error("Failed to refresh edited listing:", e);
        }
      }
    }
  };

  const isRefreshing = !!activeLog && !activeLog.completed_at;

  function formatLastRun(iso: string | null): string {
    if (!iso) return "never";
    const d = new Date(iso);
    const date = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    return `${date} ${time}`;
  }

  return (
    <>
    <div className="flex h-screen overflow-hidden bg-zinc-950">
      <div className={`flex flex-col ${selected ? "w-1/2" : "w-full"} transition-all duration-200`}>
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-zinc-800">
          <h1 className="text-xl font-bold text-white tracking-tight">CarHunt</h1>
          <div className="flex items-center gap-3">
            {!isRefreshing && (
              <span className="text-xs text-zinc-500">
                Last run: {formatLastRun(lastRefreshedAt)}
              </span>
            )}
            <button
              onClick={handleRefresh}
              disabled={isRefreshing}
              className="px-4 py-2 text-sm bg-zinc-800 hover:bg-zinc-700 text-zinc-100 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {isRefreshing ? "Refreshing..." : "Refresh Now"}
            </button>
          </div>
        </div>

        {/* Refresh status banner */}
        <RefreshBanner
          log={activeLog}
          onDone={() => setActiveLog(null)}
        />

        {/* Background job banner (retrain / refresh via background_jobs table) */}
        <JobBanner
          job={activeJob}
          onDismiss={() => setActiveJob(null)}
        />

        {/* Tabs */}
        <div className="flex border-b border-zinc-800 px-4 overflow-x-auto">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => { setTab(t.key); setFilters(DEFAULT_FILTERS); }}
              className={`px-4 py-3 text-sm font-medium whitespace-nowrap transition-colors ${
                tab === t.key
                  ? "text-white border-b-2 border-blue-500"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              {t.label}
              {counts[t.key] ? (
                <span className="ml-2 px-1.5 py-0.5 text-xs bg-zinc-700 rounded-full">
                  {counts[t.key]}
                </span>
              ) : null}
            </button>
          ))}
        </div>

        {/* Filter + sort bar (active tab only) */}
        {tab === "active" && (
          <FilterBar
            listings={listings}
            filters={filters}
            sort={sort}
            onFilters={setFilters}
            onSort={setSort}
          />
        )}

        {/* Training Data tab */}
        {tab === "training" ? (
          <TrainingDataTab />
        ) : (
          <>
            {/* Result count */}
            {!loading && listings.length > 0 && (
              <div className="px-4 pt-2 pb-1 text-xs text-zinc-500">
                {displayedListings.length === listings.length
                  ? `${listings.length} listing${listings.length !== 1 ? "s" : ""}`
                  : `${displayedListings.length} of ${listings.length} listings`}
              </div>
            )}

            {/* Listing list */}
            <div className="flex-1 overflow-y-auto p-4 pt-1 space-y-2">
              {loading ? (
                <div className="text-zinc-500 text-sm text-center py-12">Loading...</div>
              ) : displayedListings.length === 0 ? (
                <div className="text-zinc-500 text-sm text-center py-12">
                  {listings.length > 0
                    ? "No listings match your filters."
                    : isRefreshing ? "Refresh running — listings will appear when complete." : "No listings here yet."}
                </div>
              ) : (
                displayedListings.map((l) => (
                  <ListingCard
                    key={l.id}
                    listing={l}
                    selected={selected?.id === l.id}
                    onSelect={() => setSelected(selected?.id === l.id ? null : l)}
                    onAction={handleAction}
                    tab={tab}
                  />
                ))
              )}
            </div>
          </>
        )}
      </div>

      {/* Right panel — detail (only for listing tabs) */}
      {selected && tab !== "training" && (
        <div className="w-1/2 border-l border-zinc-800 overflow-y-auto">
          <ListingDetail
            listing={selected}
            onClose={() => setSelected(null)}
            onAction={handleAction}
          />
        </div>
      )}
    </div>
    <QueryChat />
    </>
  );
}
