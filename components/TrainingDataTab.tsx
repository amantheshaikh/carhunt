"use client";

import { useState, useEffect, useMemo, useCallback } from "react";
import type { TrainingRow } from "@/app/api/training-data/route";

type SortKey = "year" | "price" | "mileage" | "trim" | "powertrain" | "drivetrain";
type SortDir = "asc" | "desc";

interface EditState {
  price: string;
  mileage: string;
  trim: string;
  year: string;
  powertrain: string;
  drivetrain: string;
}

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

const POWERTRAIN_STYLES: Record<string, string> = {
  gas:      "bg-zinc-700 text-zinc-300",
  hybrid:   "bg-blue-900 text-blue-300",
  phev:     "bg-green-900 text-green-300",
  electric: "bg-emerald-900 text-emerald-300",
};

const DRIVETRAIN_STYLES: Record<string, string> = {
  awd:     "bg-orange-900 text-orange-300",
  fwd:     "bg-zinc-700 text-zinc-400",
  unknown: "bg-zinc-800 text-zinc-600",
};

function Badge({ val, styles }: { val: string | null; styles: Record<string, string> }) {
  if (!val) return <span className="text-zinc-600">—</span>;
  return (
    <span className={`px-1.5 py-0.5 text-xs rounded font-medium ${styles[val] ?? "bg-zinc-700 text-zinc-300"}`}>
      {val}
    </span>
  );
}

function InlineInput({ value, onChange, type = "text", className = "" }: {
  value: string; onChange: (v: string) => void; type?: string; className?: string;
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onClick={(e) => e.stopPropagation()}
      className={`bg-zinc-700 text-white px-1.5 py-0.5 rounded text-xs w-full focus:outline-none focus:ring-1 focus:ring-blue-500 ${className}`}
    />
  );
}

function InlineSelect({ value, options, onChange }: {
  value: string; options: string[]; onChange: (v: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onClick={(e) => e.stopPropagation()}
      className="bg-zinc-700 text-white px-1.5 py-0.5 rounded text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
    >
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}

export default function TrainingDataTab() {
  const [models, setModels] = useState<{ model: string; n: number }[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [rows, setRows] = useState<TrainingRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [sort, setSort] = useState<SortKey>("year");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [showListings, setShowListings] = useState(false);
  const [filterYear, setFilterYear] = useState<string>("");
  const [filterPowertrain, setFilterPowertrain] = useState<string>("");
  const [filterDrivetrain, setFilterDrivetrain] = useState<string>("");
  const [editingCell, setEditingCell] = useState<{ id: string; field: string } | null>(null);
  const [tempValue, setTempValue] = useState<string>("");

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
    setEditingCell(null);
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
    if (filterDrivetrain) out = out.filter((r) => (r.drivetrain ?? "unknown") === filterDrivetrain);
    return [...out].sort((a, b) => {
      let va: number | string, vb: number | string;
      switch (sort) {
        case "year":       va = a.year; vb = b.year; break;
        case "price":      va = a.price ?? 0; vb = b.price ?? 0; break;
        case "mileage":    va = a.mileage ?? 0; vb = b.mileage ?? 0; break;
        case "trim":       va = a.trim ?? ""; vb = b.trim ?? ""; break;
        case "powertrain": va = a.powertrain ?? ""; vb = b.powertrain ?? ""; break;
        case "drivetrain": va = a.drivetrain ?? ""; vb = b.drivetrain ?? ""; break;
      }
      return sortDir === "asc" ? (va < vb ? -1 : va > vb ? 1 : 0) : (va > vb ? -1 : va < vb ? 1 : 0);
    });
  }, [displayRows, filterYear, filterPowertrain, filterDrivetrain, sort, sortDir]);

  const yearStats = useMemo(() => {
    const map: Record<number, { n: number; median: number }> = {};
    for (const yr of years) {
      const prices = trainRows.filter((r) => r.year === yr).map((r) => r.price);
      map[yr] = { n: prices.length, median: Math.round(median(prices)) };
    }
    return map;
  }, [trainRows, years]);

  const colSort = (key: SortKey) => {
    if (sort === key) setSortDir((d) => d === "asc" ? "desc" : "asc");
    else { setSort(key); setSortDir("asc"); }
  };

  const handleCellClick = (rowId: string, field: string, currentValue: any) => {
    setEditingCell({ id: rowId, field });
    setTempValue(currentValue === null || currentValue === undefined ? "" : String(currentValue));
  };

  const handleCellSave = async (row: TrainingRow, field: string) => {
    if (!editingCell) return;

    let newValue: any = tempValue;
    if (field === "price" || field === "year") {
      newValue = Number(tempValue);
      if (isNaN(newValue)) return;
    } else if (field === "mileage") {
      newValue = tempValue === "" ? null : Number(tempValue);
      if (newValue !== null && isNaN(newValue)) return;
    } else if (field === "trim") {
      newValue = tempValue.trim() || null;
    }

    const originalValue = (row as any)[field];
    if (newValue === originalValue) {
      setEditingCell(null);
      return;
    }

    // Optimistically update local state
    setRows((prev) =>
      prev.map((r) => (r.id !== row.id ? r : { ...r, [field]: newValue }))
    );
    setEditingCell(null);

    try {
      await fetch(`/api/training-data?id=${row.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          [field]: newValue,
          model: row.model,
        }),
      });
    } catch (err) {
      console.error("Error saving inline edit:", err);
    }
  };

  const handleSelectSave = async (row: TrainingRow, field: string, val: string) => {
    setRows((prev) =>
      prev.map((r) => (r.id !== row.id ? r : { ...r, [field]: val }))
    );
    setEditingCell(null);
    try {
      await fetch(`/api/training-data?id=${row.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          [field]: val,
          model: row.model,
        }),
      });
    } catch (err) {
      console.error("Error saving select edit:", err);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent, row: TrainingRow, field: string) => {
    if (e.key === "Enter") {
      handleCellSave(row, field);
    } else if (e.key === "Escape") {
      setEditingCell(null);
    }
  };

  const deleteRow = async (id: string) => {
    if (!confirm("Delete this training row?")) return;
    await fetch(`/api/training-data?id=${id}`, { method: "DELETE" });
    setRows((prev) => prev.filter((r) => r.id !== id));
    if (editingCell?.id === id) { setEditingCell(null); }
  };

  const SortArrow = ({ col }: { col: SortKey }) =>
    sort === col ? (sortDir === "asc" ? " ↑" : " ↓") : "";

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header controls */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-zinc-800 flex-wrap">
        <select
          className="bg-zinc-800 text-sm text-white px-3 py-1.5 rounded border border-zinc-700 focus:outline-none"
          value={selectedModel}
          onChange={(e) => { setSelectedModel(e.target.value); setFilterYear(""); }}
        >
          {models.map((m) => (
            <option key={m.model} value={m.model}>{m.model} ({m.n})</option>
          ))}
        </select>

        <select
          className="bg-zinc-800 text-sm text-white px-3 py-1.5 rounded border border-zinc-700 focus:outline-none"
          value={filterYear}
          onChange={(e) => setFilterYear(e.target.value)}
        >
          <option value="">All years</option>
          {years.map((y) => (
            <option key={y} value={y}>{y} ({yearStats[y]?.n ?? 0} rows, med {fmt$(yearStats[y]?.median ?? null)})</option>
          ))}
        </select>

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

        <select
          className="bg-zinc-800 text-sm text-white px-3 py-1.5 rounded border border-zinc-700 focus:outline-none"
          value={filterDrivetrain}
          onChange={(e) => setFilterDrivetrain(e.target.value)}
        >
          <option value="">All drivetrains</option>
          <option value="awd">AWD</option>
          <option value="fwd">FWD</option>
          <option value="unknown">Unknown</option>
        </select>

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
                filterYear === String(yr) ? "bg-blue-600 text-white" : "bg-zinc-800 text-zinc-300 hover:bg-zinc-700"
              }`}
            >
              <span className="font-medium">{yr}</span>
              <span className="ml-1 text-zinc-400">{s.n} · {fmt$(s.median)} med</span>
            </button>
          );
        })}
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {loading ? (
          <div className="text-zinc-500 text-sm text-center py-12">Loading...</div>
        ) : (
          <table className="w-full text-sm whitespace-nowrap">
            <thead className="sticky top-0 bg-zinc-900 border-b border-zinc-800 text-zinc-400 text-xs">
              <tr>
                <th className="px-3 py-2 text-left cursor-pointer hover:text-white" onClick={() => colSort("year")}>Year<SortArrow col="year" /></th>
                <th className="px-3 py-2 text-left cursor-pointer hover:text-white" onClick={() => colSort("trim")}>Trim<SortArrow col="trim" /></th>
                <th className="px-3 py-2 text-left cursor-pointer hover:text-white" onClick={() => colSort("powertrain")}>Powertrain<SortArrow col="powertrain" /></th>
                <th className="px-3 py-2 text-left cursor-pointer hover:text-white" onClick={() => colSort("drivetrain")}>Drivetrain<SortArrow col="drivetrain" /></th>
                <th className="px-3 py-2 text-right cursor-pointer hover:text-white" onClick={() => colSort("mileage")}>Mileage<SortArrow col="mileage" /></th>
                <th className="px-3 py-2 text-right cursor-pointer hover:text-white" onClick={() => colSort("price")}>Price<SortArrow col="price" /></th>
                <th className="px-3 py-2 text-left text-zinc-500">Source</th>
                <th className="px-3 py-2 text-left text-zinc-500">Collected</th>
                <th className="px-3 py-2 text-left text-zinc-500">Link</th>
                <th className="px-3 py-2 w-28"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/50">
              {filtered.map((row) => {
                const isListing = !row.editable;
                return (
                  <tr
                    key={row.id}
                    className={`group transition-colors ${isListing ? "opacity-50" : "hover:bg-zinc-800/10"}`}
                  >
                    <td
                      onClick={() => row.editable && handleCellClick(row.id, "year", row.year)}
                      className={`px-3 py-1.5 text-zinc-300 ${row.editable ? "cursor-pointer hover:bg-zinc-800/40" : ""}`}
                    >
                      {editingCell?.id === row.id && editingCell?.field === "year" ? (
                        <input
                          autoFocus
                          type="number"
                          value={tempValue}
                          onChange={(e) => setTempValue(e.target.value)}
                          onBlur={() => handleCellSave(row, "year")}
                          onKeyDown={(e) => handleKeyDown(e, row, "year")}
                          className="bg-zinc-800 text-white px-1.5 py-0.5 rounded text-xs w-16 focus:outline-none focus:ring-1 focus:ring-blue-500 border border-zinc-700"
                        />
                      ) : (
                        row.year
                      )}
                    </td>
                    <td
                      onClick={() => row.editable && handleCellClick(row.id, "trim", row.trim)}
                      className={`px-3 py-1.5 text-zinc-200 max-w-[160px] ${row.editable ? "cursor-pointer hover:bg-zinc-800/40" : ""}`}
                    >
                      {editingCell?.id === row.id && editingCell?.field === "trim" ? (
                        <input
                          autoFocus
                          type="text"
                          value={tempValue}
                          onChange={(e) => setTempValue(e.target.value)}
                          onBlur={() => handleCellSave(row, "trim")}
                          onKeyDown={(e) => handleKeyDown(e, row, "trim")}
                          className="bg-zinc-800 text-white px-1.5 py-0.5 rounded text-xs w-32 focus:outline-none focus:ring-1 focus:ring-blue-500 border border-zinc-700"
                        />
                      ) : (
                        <span className="truncate block" title={row.trim ?? undefined}>
                          {row.trim ?? <span className="text-zinc-600">—</span>}
                        </span>
                      )}
                    </td>
                    <td
                      onClick={() => row.editable && handleCellClick(row.id, "powertrain", row.powertrain ?? "gas")}
                      className={`px-3 py-1.5 ${row.editable ? "cursor-pointer hover:bg-zinc-800/40" : ""}`}
                    >
                      {editingCell?.id === row.id && editingCell?.field === "powertrain" ? (
                        <select
                          autoFocus
                          value={tempValue}
                          onChange={(e) => handleSelectSave(row, "powertrain", e.target.value)}
                          onBlur={() => setEditingCell(null)}
                          className="bg-zinc-800 text-white px-1.5 py-0.5 rounded text-xs focus:outline-none focus:ring-1 focus:ring-blue-500 border border-zinc-700"
                        >
                          {["gas", "hybrid", "phev", "electric"].map((o) => (
                            <option key={o} value={o}>{o}</option>
                          ))}
                        </select>
                      ) : (
                        <Badge val={row.powertrain} styles={POWERTRAIN_STYLES} />
                      )}
                    </td>
                    <td
                      onClick={() => row.editable && handleCellClick(row.id, "drivetrain", row.drivetrain ?? "unknown")}
                      className={`px-3 py-1.5 ${row.editable ? "cursor-pointer hover:bg-zinc-800/40" : ""}`}
                    >
                      {editingCell?.id === row.id && editingCell?.field === "drivetrain" ? (
                        <select
                          autoFocus
                          value={tempValue}
                          onChange={(e) => handleSelectSave(row, "drivetrain", e.target.value)}
                          onBlur={() => setEditingCell(null)}
                          className="bg-zinc-800 text-white px-1.5 py-0.5 rounded text-xs focus:outline-none focus:ring-1 focus:ring-blue-500 border border-zinc-700"
                        >
                          {["awd", "fwd", "unknown"].map((o) => (
                            <option key={o} value={o}>{o}</option>
                          ))}
                        </select>
                      ) : (
                        <Badge val={row.drivetrain} styles={DRIVETRAIN_STYLES} />
                      )}
                    </td>
                    <td
                      onClick={() => row.editable && handleCellClick(row.id, "mileage", row.mileage)}
                      className={`px-3 py-1.5 text-right text-zinc-400 tabular-nums ${row.editable ? "cursor-pointer hover:bg-zinc-800/40" : ""}`}
                    >
                      {editingCell?.id === row.id && editingCell?.field === "mileage" ? (
                        <input
                          autoFocus
                          type="number"
                          value={tempValue}
                          onChange={(e) => setTempValue(e.target.value)}
                          onBlur={() => handleCellSave(row, "mileage")}
                          onKeyDown={(e) => handleKeyDown(e, row, "mileage")}
                          className="bg-zinc-800 text-white px-1.5 py-0.5 rounded text-xs w-24 text-right focus:outline-none focus:ring-1 focus:ring-blue-500 border border-zinc-700"
                        />
                      ) : (
                        fmtMi(row.mileage)
                      )}
                    </td>
                    <td
                      onClick={() => row.editable && handleCellClick(row.id, "price", row.price)}
                      className={`px-3 py-1.5 text-right text-white font-medium tabular-nums ${row.editable ? "cursor-pointer hover:bg-zinc-800/40" : ""}`}
                    >
                      {editingCell?.id === row.id && editingCell?.field === "price" ? (
                        <input
                          autoFocus
                          type="number"
                          value={tempValue}
                          onChange={(e) => setTempValue(e.target.value)}
                          onBlur={() => handleCellSave(row, "price")}
                          onKeyDown={(e) => handleKeyDown(e, row, "price")}
                          className="bg-zinc-800 text-white px-1.5 py-0.5 rounded text-xs w-24 text-right focus:outline-none focus:ring-1 focus:ring-blue-500 border border-zinc-700"
                        />
                      ) : (
                        fmt$(row.price)
                      )}
                    </td>
                    <td className="px-3 py-1.5 text-zinc-500 text-xs">{row.source}</td>
                    <td className="px-3 py-1.5 text-zinc-500 text-xs">
                      {row.collected_at ? new Date(row.collected_at).toLocaleDateString() : "—"}
                    </td>
                    <td className="px-3 py-1.5">
                      {row.listing_url ? (
                        <a href={row.listing_url} target="_blank" rel="noopener noreferrer"
                          className="text-zinc-500 hover:text-blue-400 text-xs">↗</a>
                      ) : <span className="text-zinc-700">—</span>}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      {row.editable && (
                        <div className="flex gap-1 opacity-0 group-hover:opacity-100 justify-end">
                          <button
                            onClick={() => deleteRow(row.id)}
                            className="px-2 py-0.5 text-xs text-zinc-500 hover:text-red-400 rounded transition-colors"
                            title="Delete training row"
                          >
                            ✕ Delete
                          </button>
                        </div>
                      )}
                    </td>
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
