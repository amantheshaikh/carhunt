"use client";

import { useState, useEffect, useMemo, useCallback } from "react";
import type { TrainingRow } from "@/app/api/training-data/route";

type SortKey = "year" | "price" | "mileage" | "trim" | "powertrain";
type SortDir = "asc" | "desc";

function fmt$(n: number | null) {
  return n == null ? "—" : `$${n.toLocaleString()}`;
}
function fmtMi(n: number | null) {
  return n == null ? "—" : `${n.toLocaleString()} mi`;
}

function median(vals: number[]) {
  if (!vals.length) return 0;
  const s = [...vals].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

interface EditState {
  price: string;
  mileage: string;
  trim: string;
  year: string;
  model: string;
  powertrain: string;
}

const POWERTRAIN_STYLES: Record<string, string> = {
  gas:      "bg-zinc-700 text-zinc-300",
  hybrid:   "bg-blue-900 text-blue-300",
  phev:     "bg-green-900 text-green-300",
  electric: "bg-emerald-900 text-emerald-300",
};

export default function TrainingDataTab() {
  const [models, setModels] = useState<{ model: string; n: number }[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [rows, setRows] = useState<TrainingRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [sort, setSort] = useState<SortKey>("year");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [editId, setEditId] = useState<string | null>(null);
  const [editState, setEditState] = useState<EditState>({ price: "", mileage: "", trim: "", year: "", model: "", powertrain: "gas" });
  const [showListings, setShowListings] = useState(false);
  const [saving, setSaving] = useState(false);
  const [filterYear, setFilterYear] = useState<string>("");
  const [filterPowertrain, setFilterPowertrain] = useState<string>("");

  useEffect(() => {
    fetch("/api/training-data")
      .then((r) => r.json())
      .then((data) => {
        setModels(data);
        if (data.length) setSelectedModel(data.find((m: { model: string }) => m.model === "tucson hybrid")?.model ?? data[0].model);
      });
  }, []);

  const loadRows = useCallback((model: string) => {
    if (!model) return;
    setLoading(true);
    fetch(`/api/training-data?model=${encodeURIComponent(model)}`)
      .then((r) => r.json())
      .then((data) => { setRows(data); setLoading(false); });
  }, []);

  useEffect(() => {
    if (selectedModel) loadRows(selectedModel);
  }, [selectedModel, loadRows]);

  const trainRows = useMemo(() => rows.filter((r) => r.editable), [rows]);
  const listingRows = useMemo(() => rows.filter((r) => !r.editable), [rows]);
  const displayRows = useMemo(() => showListings ? rows : trainRows, [showListings, rows, trainRows]);

  const years = useMemo(() => [...new Set(trainRows.map((r) => r.year))].sort((a, b) => b - a), [trainRows]);

  const filtered = useMemo(() => {
    let out = displayRows;
    if (filterYear) out = out.filter((r) => String(r.year) === filterYear);
    if (filterPowertrain) out = out.filter((r) => (r.powertrain ?? "gas") === filterPowertrain);
    return [...out].sort((a, b) => {
      let va: number | string, vb: number | string;
      switch (sort) {
        case "year":       va = a.year; vb = b.year; break;
        case "price":      va = a.price ?? 0; vb = b.price ?? 0; break;
        case "mileage":    va = a.mileage ?? 0; vb = b.mileage ?? 0; break;
        case "trim":       va = a.trim ?? ""; vb = b.trim ?? ""; break;
        case "powertrain": va = a.powertrain ?? ""; vb = b.powertrain ?? ""; break;
      }
      return sortDir === "asc" ? (va < vb ? -1 : va > vb ? 1 : 0) : (va > vb ? -1 : va < vb ? 1 : 0);
    });
  }, [displayRows, filterYear, filterPowertrain, sort, sortDir]);

  // Per-year stats for training rows
  const yearStats = useMemo(() => {
    const map: Record<number, { n: number; median: number; min: number; max: number }> = {};
    for (const yr of years) {
      const prices = trainRows.filter((r) => r.year === yr).map((r) => r.price);
      map[yr] = { n: prices.length, median: Math.round(median(prices)), min: Math.min(...prices), max: Math.max(...prices) };
    }
    return map;
  }, [trainRows, years]);

  const colSort = (key: SortKey) => {
    if (sort === key) setSortDir((d) => d === "asc" ? "desc" : "asc");
    else { setSort(key); setSortDir("asc"); }
  };

  const startEdit = (row: TrainingRow) => {
    setEditId(row.id);
    setEditState({
      price: String(row.price),
      mileage: String(row.mileage ?? ""),
      trim: row.trim ?? "",
      year: String(row.year),
      model: row.model,
      powertrain: row.powertrain ?? "gas",
    });
  };

  const saveEdit = async () => {
    if (!editId) return;
    setSaving(true);
    await fetch(`/api/training-data?id=${editId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        price: Number(editState.price),
        mileage: editState.mileage ? Number(editState.mileage) : null,
        trim: editState.trim || null,
        year: Number(editState.year),
        model: editState.model,
        powertrain: editState.powertrain || "gas",
      }),
    });
    setSaving(false);
    setEditId(null);
    loadRows(selectedModel);
  };

  const deleteRow = async (id: string) => {
    if (!confirm("Delete this training row?")) return;
    await fetch(`/api/training-data?id=${id}`, { method: "DELETE" });
    setRows((prev) => prev.filter((r) => r.id !== id));
  };

  const SortArrow = ({ col }: { col: SortKey }) =>
    sort === col ? (sortDir === "asc" ? " ↑" : " ↓") : "";

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header controls */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-zinc-800 flex-wrap">
        {/* Model picker */}
        <select
          className="bg-zinc-800 text-sm text-white px-3 py-1.5 rounded border border-zinc-700 focus:outline-none"
          value={selectedModel}
          onChange={(e) => { setSelectedModel(e.target.value); setFilterYear(""); }}
        >
          {models.map((m) => (
            <option key={m.model} value={m.model}>
              {m.model} ({m.n})
            </option>
          ))}
        </select>

        {/* Year filter */}
        <select
          className="bg-zinc-800 text-sm text-white px-3 py-1.5 rounded border border-zinc-700 focus:outline-none"
          value={filterYear}
          onChange={(e) => setFilterYear(e.target.value)}
        >
          <option value="">All years</option>
          {years.map((y) => (
            <option key={y} value={y}>
              {y} ({yearStats[y]?.n ?? 0} rows, med {fmt$(yearStats[y]?.median ?? null)})
            </option>
          ))}
        </select>

        {/* Powertrain filter */}
        <select
          className="bg-zinc-800 text-sm text-white px-3 py-1.5 rounded border border-zinc-700 focus:outline-none"
          value={filterPowertrain}
          onChange={(e) => setFilterPowertrain(e.target.value)}
        >
          <option value="">All powertrains</option>
          <option value="gas">Gas</option>
          <option value="hybrid">Hybrid</option>
          <option value="phev">PHEV</option>
          <option value="electric">Electric</option>
        </select>

        {/* Toggle listings */}
        <label className="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={showListings}
            onChange={(e) => setShowListings(e.target.checked)}
            className="accent-blue-500"
          />
          Show listing-sourced rows ({listingRows.length})
        </label>

        <span className="ml-auto text-xs text-zinc-500">
          {filtered.length} rows shown · {trainRows.length} editable
        </span>
      </div>

      {/* Per-year stats strip */}
      <div className="px-4 py-2 flex gap-3 flex-wrap border-b border-zinc-800/50">
        {years.map((yr) => {
          const s = yearStats[yr];
          if (!s) return null;
          return (
            <button
              key={yr}
              onClick={() => setFilterYear(filterYear === String(yr) ? "" : String(yr))}
              className={`text-xs px-2 py-1 rounded transition-colors ${
                filterYear === String(yr)
                  ? "bg-blue-600 text-white"
                  : "bg-zinc-800 text-zinc-300 hover:bg-zinc-700"
              }`}
            >
              <span className="font-medium">{yr}</span>
              <span className="ml-1 text-zinc-400">{s.n} · {fmt$(s.median)} med</span>
            </button>
          );
        })}
      </div>

      {/* Table */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="text-zinc-500 text-sm text-center py-12">Loading...</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-zinc-900 border-b border-zinc-800 text-zinc-400 text-xs">
              <tr>
                <th className="px-3 py-2 text-left text-zinc-500">ID</th>
                <th className="px-3 py-2 text-left cursor-pointer hover:text-white" onClick={() => colSort("year")}>
                  Year<SortArrow col="year" />
                </th>
                <th className="px-3 py-2 text-left cursor-pointer hover:text-white" onClick={() => colSort("trim")}>
                  Trim<SortArrow col="trim" />
                </th>
                <th className="px-3 py-2 text-left cursor-pointer hover:text-white" onClick={() => colSort("powertrain")}>
                  Powertrain<SortArrow col="powertrain" />
                </th>
                <th className="px-3 py-2 text-right cursor-pointer hover:text-white" onClick={() => colSort("mileage")}>
                  Mileage<SortArrow col="mileage" />
                </th>
                <th className="px-3 py-2 text-right cursor-pointer hover:text-white" onClick={() => colSort("price")}>
                  Price<SortArrow col="price" />
                </th>
                <th className="px-3 py-2 text-left text-zinc-500">Source</th>
                <th className="px-3 py-2 text-left text-zinc-500">Date</th>
                <th className="px-3 py-2 text-left text-zinc-500">Link</th>
                <th className="px-3 py-2 w-24"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/50">
              {filtered.map((row) => {
                const isEditing = editId === row.id;
                const isListing = !row.editable;
                return (
                  <tr
                    key={row.id}
                    className={`group ${isListing ? "opacity-50" : "hover:bg-zinc-800/30"}`}
                  >
                    {isEditing ? (
                      <>
                        <td className="px-3 py-1.5 text-zinc-600 text-xs tabular-nums">{row.id}</td>
                        <td className="px-3 py-1.5">
                          <input
                            className="w-16 bg-zinc-700 text-white px-1.5 py-0.5 rounded text-sm"
                            value={editState.year}
                            onChange={(e) => setEditState((s) => ({ ...s, year: e.target.value }))}
                          />
                        </td>
                        <td className="px-3 py-1.5">
                          <input
                            className="w-full bg-zinc-700 text-white px-1.5 py-0.5 rounded text-sm"
                            value={editState.trim}
                            onChange={(e) => setEditState((s) => ({ ...s, trim: e.target.value }))}
                          />
                        </td>
                        <td className="px-3 py-1.5">
                          <select
                            className="bg-zinc-700 text-white px-1.5 py-0.5 rounded text-sm"
                            value={editState.powertrain}
                            onChange={(e) => setEditState((s) => ({ ...s, powertrain: e.target.value }))}
                          >
                            <option value="gas">gas</option>
                            <option value="hybrid">hybrid</option>
                            <option value="phev">phev</option>
                            <option value="electric">electric</option>
                          </select>
                        </td>
                        <td className="px-3 py-1.5 text-right">
                          <input
                            className="w-24 bg-zinc-700 text-white px-1.5 py-0.5 rounded text-sm text-right"
                            value={editState.mileage}
                            onChange={(e) => setEditState((s) => ({ ...s, mileage: e.target.value }))}
                          />
                        </td>
                        <td className="px-3 py-1.5 text-right">
                          <input
                            className="w-24 bg-zinc-700 text-white px-1.5 py-0.5 rounded text-sm text-right"
                            value={editState.price}
                            onChange={(e) => setEditState((s) => ({ ...s, price: e.target.value }))}
                          />
                        </td>
                        <td className="px-3 py-1.5">
                          <input
                            className="w-32 bg-zinc-700 text-white px-1.5 py-0.5 rounded text-sm"
                            value={editState.model}
                            onChange={(e) => setEditState((s) => ({ ...s, model: e.target.value }))}
                            placeholder="model"
                          />
                        </td>
                        <td />
                        <td />
                        <td className="px-3 py-1.5">
                          <div className="flex gap-1">
                            <button
                              onClick={saveEdit}
                              disabled={saving}
                              className="text-xs px-2 py-0.5 bg-blue-600 hover:bg-blue-500 text-white rounded disabled:opacity-50"
                            >
                              {saving ? "…" : "Save"}
                            </button>
                            <button
                              onClick={() => setEditId(null)}
                              className="text-xs px-2 py-0.5 bg-zinc-700 hover:bg-zinc-600 text-zinc-300 rounded"
                            >
                              Cancel
                            </button>
                          </div>
                        </td>
                      </>
                    ) : (
                      <>
                        <td className="px-3 py-2 text-zinc-600 text-xs tabular-nums">{row.id}</td>
                        <td
                          className={`px-3 py-2 text-zinc-300 ${!isListing ? "cursor-pointer hover:text-white hover:bg-zinc-800/50" : ""}`}
                          onClick={!isListing ? () => startEdit(row) : undefined}
                        >{row.year}</td>
                        <td
                          className={`px-3 py-2 text-zinc-200 max-w-xs truncate ${!isListing ? "cursor-pointer hover:text-white hover:bg-zinc-800/50" : ""}`}
                          title={row.trim ?? undefined}
                          onClick={!isListing ? () => startEdit(row) : undefined}
                        >
                          {row.trim ?? <span className="text-zinc-600">—</span>}
                        </td>
                        <td className="px-3 py-2">
                          {row.powertrain ? (
                            <span className={`px-1.5 py-0.5 text-xs rounded font-medium ${POWERTRAIN_STYLES[row.powertrain] ?? "bg-zinc-700 text-zinc-300"}`}>
                              {row.powertrain}
                            </span>
                          ) : <span className="text-zinc-600">—</span>}
                        </td>
                        <td
                          className={`px-3 py-2 text-right text-zinc-400 tabular-nums ${!isListing ? "cursor-pointer hover:text-white hover:bg-zinc-800/50" : ""}`}
                          onClick={!isListing ? () => startEdit(row) : undefined}
                        >{fmtMi(row.mileage)}</td>
                        <td
                          className={`px-3 py-2 text-right text-white font-medium tabular-nums ${!isListing ? "cursor-pointer hover:bg-zinc-800/50" : ""}`}
                          onClick={!isListing ? () => startEdit(row) : undefined}
                        >{fmt$(row.price)}</td>
                        <td className="px-3 py-2 text-zinc-500 text-xs">{row.source}</td>
                        <td className="px-3 py-2 text-zinc-600 text-xs whitespace-nowrap">
                          {row.collected_at ? new Date(row.collected_at).toLocaleDateString() : "—"}
                        </td>
                        <td className="px-3 py-2">
                          {row.listing_url ? (
                            <a
                              href={row.listing_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-xs text-blue-400 hover:text-blue-300 underline"
                            >
                              View ↗
                            </a>
                          ) : null}
                        </td>
                        <td className="px-3 py-2">
                          {!isListing && (
                            <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                              <button
                                onClick={() => deleteRow(row.id)}
                                className="text-xs px-2 py-0.5 bg-red-900/50 hover:bg-red-800 text-red-300 rounded"
                              >
                                Del
                              </button>
                            </div>
                          )}
                        </td>
                      </>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
