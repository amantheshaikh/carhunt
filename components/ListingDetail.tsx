"use client";

import { useState, useEffect, useCallback } from "react";
import type { Listing } from "@/lib/types";
import { getDealCategory, getDealCategoryStyle, getValueScore } from "@/lib/vehicle-tags";


interface Props {
  listing: Listing;
  onClose: () => void;
  onAction: (id: string, action: string, value?: unknown) => void;
}

export default function ListingDetail({ listing: l, onClose, onAction }: Props) {
  const [editingField, setEditingField] = useState<string | null>(null);
  const [editValue, setEditValue] = useState<string>("");
  const [allowedTrims, setAllowedTrims] = useState<string[]>([]);
  const [msrpByTrim, setMsrpByTrim] = useState<Record<string, number> | null>(null);

  // Parse existing trim into base + drivetrain parts
  const parseTrim = useCallback((raw: string | null) => {
    if (!raw) return { base: "", drivetrain: "" };
    const t = raw.toUpperCase();
    const drv = t.includes("AWD") || t.includes("4MOTION") ? "AWD"
              : t.includes("FWD") ? "FWD" : "";
    const base = raw.replace(/\s*(AWD|FWD|4MOTION|4WD)\s*/gi, "").trim();
    return { base, drivetrain: drv };
  }, []);

  const [trimBase, setTrimBase] = useState(() => parseTrim(l.trim).base);
  const [trimDrv, setTrimDrv]   = useState(() => parseTrim(l.trim).drivetrain);
  const [drivetrain, setDrivetrain] = useState<"fwd" | "awd" | "">(l.drivetrain ?? "");
  const [userNotes, setUserNotes] = useState(l.user_notes ?? "");
  const [isSavingNotes, setIsSavingNotes] = useState(false);

  useEffect(() => {
    setUserNotes(l.user_notes ?? "");
  }, [l.id, l.user_notes]);

  useEffect(() => {
    fetch(`/api/trims?make=${encodeURIComponent(l.make)}&model=${encodeURIComponent(l.model)}&year=${l.year}`)
      .then((r) => r.json())
      .then((d) => {
        setAllowedTrims(d.trims ?? []);
        setMsrpByTrim(d.msrp ?? null);
      });
  }, [l.make, l.model]);

  const startEdit = (field: string, current: string | number | null) => {
    setEditingField(field);
    setEditValue(String(current ?? ""));
  };

  const commitEdit = async (field: string) => {
    setEditingField(null);
    const numVal = Number(editValue);
    const payload: Record<string, unknown> = {};
    if (field === "mileage" && editValue !== "" && numVal !== l.mileage) payload.mileage = numVal;
    if (field === "price"   && editValue !== "" && numVal !== l.price)   payload.price   = numVal;
    if (field === "year"    && editValue !== "" && numVal !== l.year)     payload.year    = numVal;
    if (Object.keys(payload).length === 0) return;
    await fetch(`/api/listings/${l.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    onAction(l.id, "edited");
  };

  const commitTrim = async (base: string, drv: string) => {
    setEditingField(null);
    const newTrim = [base, drv].filter(Boolean).join(" ") || null;
    if (newTrim === l.trim) return;
    await fetch(`/api/listings/${l.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trim: newTrim }),
    });
    onAction(l.id, "edited");
  };

  const commitDrivetrain = async (val: "fwd" | "awd" | "") => {
    setDrivetrain(val);
    await fetch(`/api/listings/${l.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ drivetrain: val || null }),
    });
    onAction(l.id, "edited");
  };

  const handleSaveNotes = async () => {
    if (userNotes === (l.user_notes ?? "")) return;
    setIsSavingNotes(true);
    try {
      await fetch(`/api/listings/${l.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_notes: userNotes || null }),
      });
      onAction(l.id, "edited");
    } catch (e) {
      console.error("Failed to save notes:", e);
    } finally {
      setIsSavingNotes(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent, field: string) => {
    if (e.key === "Enter")  { e.preventDefault(); commitEdit(field); }
    if (e.key === "Escape") { setEditingField(null); }
  };

  // Removed contact and note handling

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-bold text-white">
            {l.year} {l.make} {l.model} {l.trim && <span className="text-zinc-400 font-normal text-base">{l.trim}</span>}
          </h2>
          <p className="text-zinc-400 text-sm capitalize">
            {({cargurus:"CarGurus",carscom:"Cars.com",carmax:"CarMax",carvana:"Carvana",dealer:"Dealer"}[l.source]??l.source.replace("_new",""))} · {l.condition === "new" ? "New" : "Used"}{l.body_type ? ` · ${l.body_type}` : ""}
          </p>
        </div>
        <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300 text-xl leading-none">×</button>
      </div>

      {/* Photo */}
      {l.photos_url?.[0] && (
        <img src={l.photos_url[0]} alt="" className="w-full h-48 object-cover rounded-xl" />
      )}

      {/* Key stats — editable fields click to edit in place */}
      <div className="grid grid-cols-3 gap-3">

        {/* Price — editable */}
        <div
          className="bg-zinc-900 rounded-lg p-3 cursor-text group"
          onClick={() => editingField !== "price" && startEdit("price", l.price)}
        >
          <p className="text-xs text-zinc-500">Price</p>
          {editingField === "price" ? (
            <input autoFocus type="number" value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onBlur={() => commitEdit("price")}
              onKeyDown={(e) => onKeyDown(e, "price")}
              className="w-full bg-transparent text-sm font-medium text-white mt-0.5 focus:outline-none border-b border-blue-500"
            />
          ) : (
            <p className="text-sm font-medium text-white mt-0.5 group-hover:text-blue-300 transition-colors">
              ${l.price?.toLocaleString()}
            </p>
          )}
        </div>

        {/* Mileage — editable */}
        <div
          className="bg-zinc-900 rounded-lg p-3 cursor-text group"
          onClick={() => editingField !== "mileage" && startEdit("mileage", l.mileage)}
        >
          <p className="text-xs text-zinc-500">Mileage</p>
          {editingField === "mileage" ? (
            <input autoFocus type="number" value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onBlur={() => commitEdit("mileage")}
              onKeyDown={(e) => onKeyDown(e, "mileage")}
              placeholder="e.g. 10519"
              className="w-full bg-transparent text-sm font-medium text-white mt-0.5 focus:outline-none border-b border-blue-500"
            />
          ) : (
            <p className={`text-sm font-medium mt-0.5 group-hover:text-blue-300 transition-colors ${l.mileage ? "text-white" : "text-zinc-600 italic"}`}>
              {l.mileage ? `${l.mileage.toLocaleString()} mi` : "tap to add"}
            </p>
          )}
        </div>

        {/* Trim — dropdown from cars.json + drivetrain toggle */}
        <div
          className="bg-zinc-900 rounded-lg p-3 cursor-pointer group col-span-2"
          onClick={() => editingField !== "trim" && setEditingField("trim")}
        >
          <p className="text-xs text-zinc-500">Trim</p>
          {editingField === "trim" ? (
            <div className="flex gap-2 mt-1" onClick={(e) => e.stopPropagation()}>
              <select
                autoFocus
                value={trimBase}
                onChange={(e) => setTrimBase(e.target.value)}
                onBlur={() => commitTrim(trimBase, trimDrv)}
                className="flex-1 bg-zinc-800 border border-blue-500 text-zinc-100 text-sm rounded-lg px-2 py-1 focus:outline-none"
              >
                <option value="">— select —</option>
                {allowedTrims.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
              <select
                value={trimDrv}
                onChange={(e) => { const drv = e.target.value; setTrimDrv(drv); commitTrim(trimBase, drv); }}
                className="bg-zinc-800 border border-zinc-700 text-zinc-100 text-sm rounded-lg px-2 py-1 focus:outline-none focus:border-blue-500"
              >
                <option value="">—</option>
                <option value="FWD">FWD</option>
                <option value="AWD">AWD</option>
              </select>
            </div>
          ) : (
            <p className={`text-sm font-medium mt-0.5 group-hover:text-blue-300 transition-colors ${l.trim ? "text-white" : "text-zinc-600 italic"}`}>
              {l.trim ?? "tap to set"}
            </p>
          )}
        </div>

        {/* Drivetrain — manual override */}
        <div className="bg-zinc-900 rounded-lg p-3">
          <p className="text-xs text-zinc-500">Drivetrain</p>
          <div className="flex gap-1 mt-1">
            {(["", "fwd", "awd"] as const).map((v) => (
              <button
                key={v}
                onClick={() => commitDrivetrain(v)}
                className={`px-2 py-0.5 rounded text-xs font-medium transition-colors ${
                  drivetrain === v
                    ? "bg-blue-600 text-white"
                    : "bg-zinc-800 text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {v === "" ? "Auto" : v.toUpperCase()}
              </button>
            ))}
          </div>
          {!drivetrain && (
            <p className="text-xs text-zinc-600 mt-1">
              auto-detected
            </p>
          )}
        </div>

        {/* Year — editable */}
        <div
          className="bg-zinc-900 rounded-lg p-3 cursor-text group"
          onClick={() => editingField !== "year" && startEdit("year", l.year)}
        >
          <p className="text-xs text-zinc-500">Year</p>
          {editingField === "year" ? (
            <input autoFocus type="number" value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onBlur={() => commitEdit("year")}
              onKeyDown={(e) => onKeyDown(e, "year")}
              className="w-full bg-transparent text-sm font-medium text-white mt-0.5 focus:outline-none border-b border-blue-500"
            />
          ) : (
            <p className="text-sm font-medium text-white mt-0.5 group-hover:text-blue-300 transition-colors">
              {l.year}
            </p>
          )}
        </div>

        {/* Title — read-only */}
        <div className="bg-zinc-900 rounded-lg p-3">
          <p className="text-xs text-zinc-500">Title</p>
          <p className="text-sm font-medium text-white mt-0.5 capitalize">{l.title_status ?? "Unknown"}</p>
        </div>

        {/* Accidents — read-only */}
        <div className="bg-zinc-900 rounded-lg p-3">
          <p className="text-xs text-zinc-500">Accidents</p>
          <p className="text-sm font-medium text-white mt-0.5">{l.accident_count != null ? String(l.accident_count) : "Unknown"}</p>
        </div>

        {/* vs market — read-only */}
        {l.fair_price && (
          <div className="bg-zinc-900 rounded-lg p-3 col-span-3">
            <p className="text-xs text-zinc-500">vs market</p>
            <p className={`text-sm font-medium mt-0.5 ${l.price < l.fair_price ? "text-green-400" : "text-red-400"}`}>
              {l.price < l.fair_price
                ? `-$${(l.fair_price - l.price).toLocaleString()} (${Math.round((l.fair_price - l.price) / l.fair_price * 100)}% below)`
                : `+$${(l.price - l.fair_price).toLocaleString()} (${Math.round((l.price - l.fair_price) / l.fair_price * 100)}% above)`}
            </p>
          </div>
        )}

        {/* MSRP / depreciation — read-only */}
        {(() => {
          if (!msrpByTrim || !l.trim) return null;
          const baseTrim = l.trim.replace(/\s*(AWD|FWD|4MOTION|4WD)\s*/gi, "").trim();
          const drv = (l.drivetrain || "").toLowerCase();
          // Try drivetrain-specific key first (e.g. "SE R-Line Black AWD"), then base trim
          const lookupKeys = drv ? [`${baseTrim} ${drv.toUpperCase()}`, baseTrim] : [baseTrim];
          const sortedEntries = Object.entries(msrpByTrim).sort((a, b) => b[0].length - a[0].length);
          const msrp = lookupKeys
            .map(lk => sortedEntries.find(([k]) => lk.toLowerCase().startsWith(k.toLowerCase()))?.[1])
            .find(Boolean);
          if (!msrp) return null;
          const retained = Math.round((l.price / msrp) * 100);
          const depreciation = 100 - retained;
          return (
            <div className="bg-zinc-900 rounded-lg p-3 col-span-3">
              <p className="text-xs text-zinc-500">vs MSRP when new</p>
              <div className="flex items-baseline gap-2 mt-0.5">
                <p className={`text-sm font-medium ${depreciation >= 8 ? "text-green-400" : "text-yellow-400"}`}>
                  -{depreciation}% (${(msrp - l.price).toLocaleString()} off)
                </p>
                <p className="text-xs text-zinc-500">MSRP was ${msrp.toLocaleString()}</p>
              </div>
              <div className="w-full bg-zinc-800 rounded-full h-1.5 mt-1.5">
                <div
                  className={`h-1.5 rounded-full ${depreciation >= 8 ? "bg-green-500" : "text-yellow-500 bg-yellow-500"}`}
                  style={{ width: `${Math.min(100, depreciation * 3)}%` }}
                />
              </div>
              <p className="text-xs text-zinc-600 mt-0.5">Typical used: 10–20% below MSRP for low-mileage 1yr-old</p>
            </div>
          );
        })()}
      </div>

      {/* Qualitative insights */}
      {l.insights && (l.insights.cpo || (l.insights.packages && l.insights.packages.length > 0) || l.insights.price_drop || l.insights.owner_notes) && (
        <div className="space-y-2">
          <p className="text-sm font-medium text-zinc-300">Listing Insights</p>
          <div className="flex flex-wrap gap-2">
            {l.insights.cpo && (
              <span className="px-2 py-1 text-xs rounded font-semibold bg-violet-900/70 text-violet-300 border border-violet-700">
                Certified Pre-Owned
              </span>
            )}
            {l.insights.price_drop && (
              <span className="px-2 py-1 text-xs rounded font-semibold bg-amber-900/70 text-amber-300 border border-amber-700">
                Price drop: ${l.insights.price_drop.toLocaleString()}
              </span>
            )}
            {(l.insights.packages || []).map((pkg) => (
              <span key={pkg} className="px-2 py-1 text-xs rounded bg-zinc-800 text-zinc-300 border border-zinc-700">
                {pkg}
              </span>
            ))}
          </div>
          {l.insights.owner_notes && (
            <p className="text-xs text-zinc-400 italic">{l.insights.owner_notes}</p>
          )}
        </div>
      )}

      {/* Personal Notes */}
      <div className="space-y-2 pt-2 border-t border-zinc-800">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium text-zinc-300">My Notes</p>
          {isSavingNotes ? (
            <span className="text-[10px] text-zinc-500 font-mono">Saving...</span>
          ) : (
            userNotes !== (l.user_notes ?? "") && (
              <button
                onClick={handleSaveNotes}
                className="text-[10px] bg-blue-600 hover:bg-blue-500 text-white font-semibold px-2 py-0.5 rounded transition-colors"
              >
                Save Changes
              </button>
            )
          )}
        </div>
        <textarea
          value={userNotes}
          onChange={(e) => setUserNotes(e.target.value)}
          onBlur={handleSaveNotes}
          placeholder="Add your own private notes about this listing (negotiation, condition, owner replies, etc.). Auto-saves on blur."
          className="w-full h-24 bg-zinc-900 border border-zinc-800 rounded-lg p-3 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-all resize-none"
        />
      </div>

      {/* Score Breakdown */}
      {l.value_score != null && (
        <div className="space-y-3">
          {/* Primary score header */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <p className="text-sm font-medium text-zinc-300">Deal Rating</p>
                <span className="text-xs text-zinc-500 bg-zinc-800/60 px-1.5 py-0.5 rounded font-mono">
                  {l.kbb_price || l.shap_values?.kbb_pred ? "Stacked ML + KBB Blend" : l.shap_values?.knn_pred ? "Stacked Blend" : "Gradient Boosting"}
                </span>
                {getDealCategory(l.value_score) && (
                  <span className={`px-2 py-0.5 text-xs font-semibold rounded-md ${getDealCategoryStyle(l.value_score)}`}>
                    {getDealCategory(l.value_score)}
                  </span>
                )}
              </div>
              <span className="text-xs text-zinc-600">{l.value_score} / 100</span>
            </div>
            <div className="w-full bg-zinc-800 rounded-full h-2">
              <div
                className={`h-2 rounded-full transition-all ${
                  l.value_score >= 88 ? "bg-emerald-500" :
                  l.value_score >= 72 ? "bg-green-500" :
                  l.value_score >= 58 ? "bg-blue-500" : "bg-zinc-500"
                }`}
                style={{ width: `${Math.min(100, Math.max(0, l.value_score))}%` }}
              />
            </div>
            {l.fair_price && (() => {
              const diff = l.fair_price - l.price;
              const pct  = ((Math.abs(diff) / l.fair_price) * 100).toFixed(1);
              const kbbVal = l.kbb_price ?? l.shap_values?.kbb_pred;
              const mlVal = l.shap_values?.ml_pred ?? l.shap_values?.predicted;
              return (
                <div className="space-y-0.5">
                  <p className={`text-xs font-medium ${diff > 0 ? "text-green-400" : "text-red-400"}`}>
                    {diff > 0
                      ? `$${diff.toLocaleString()} (${pct}%) below fair value of $${l.fair_price.toLocaleString()}`
                      : `$${Math.abs(diff).toLocaleString()} (${pct}%) above fair value of $${l.fair_price.toLocaleString()}`}
                  </p>
                  {(kbbVal || mlVal) && (
                    <p className="text-[11px] text-zinc-400 font-mono">
                      {mlVal ? `ML Model: $${mlVal.toLocaleString()}` : ""}
                      {mlVal && kbbVal ? " · " : ""}
                      {kbbVal ? `KBB Fair Value: $${kbbVal.toLocaleString()}` : ""}
                    </p>
                  )}
                </div>
              );
            })()}
          </div>

          {/* Ensemble Estimator & Market Benchmarks */}
          {l.shap_values && l.shap_values.gb_pred !== undefined && (
            <div className="space-y-2 pt-2 border-t border-zinc-800">
              <p className="text-xs font-medium text-zinc-400">Ensemble Estimator & Market Benchmarks</p>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                {[
                  {
                    name: "Gradient Boosting (GB)",
                    shortName: "GB Model",
                    pred: l.shap_values.gb_pred,
                    weight: (l.shap_values.meta_weights?.[0] ?? 1.0) * (l.shap_values.ml_weight ?? 1.0),
                    color: "bg-purple-500",
                    textColor: "text-purple-300",
                    bgColor: "bg-purple-950/20 border-purple-900/30",
                  },
                  {
                    name: "K-Nearest Neighbors (KNN)",
                    shortName: "KNN Model",
                    pred: l.shap_values.knn_pred,
                    weight: (l.shap_values.meta_weights?.[1] ?? 0.0) * (l.shap_values.ml_weight ?? 1.0),
                    color: "bg-amber-500",
                    textColor: "text-amber-300",
                    bgColor: "bg-amber-950/20 border-amber-900/30",
                  },
                  {
                    name: "Ridge Regression",
                    shortName: "Ridge Model",
                    pred: l.shap_values.ridge_pred,
                    weight: (l.shap_values.meta_weights?.[2] ?? 0.0) * (l.shap_values.ml_weight ?? 1.0),
                    color: "bg-cyan-500",
                    textColor: "text-cyan-300",
                    bgColor: "bg-cyan-950/20 border-cyan-900/30",
                  },
                  {
                    name: "Kelley Blue Book (KBB)",
                    shortName: "KBB Value",
                    pred: l.kbb_price ?? l.shap_values.kbb_pred,
                    weight: l.shap_values.kbb_weight ?? (l.kbb_price ? 0.3 : 0.0),
                    color: "bg-emerald-500",
                    textColor: "text-emerald-300",
                    bgColor: "bg-emerald-950/20 border-emerald-900/30",
                  }
                ].map((model, idx) => {
                  if (model.pred == null) return null;
                  const score = getValueScore(l.price, model.pred, l.shap_values?.residual_std);
                  const weightPct = Math.round(model.weight * 100);
                  if (weightPct === 0 && model.weight === 0) return null;
                  return (
                    <div key={idx} className={`p-2.5 rounded-lg border flex flex-col justify-between ${model.bgColor}`}>
                      <div>
                        <div className="flex items-center justify-between">
                          <span className="text-[10px] font-semibold text-zinc-400 tracking-wider uppercase truncate max-w-[70%]">
                            {model.shortName}
                          </span>
                          <span className={`text-[10px] font-bold ${model.textColor}`}>
                            {weightPct}%
                          </span>
                        </div>
                        <p className="text-xs font-mono font-bold text-zinc-100 mt-1">
                          ${model.pred.toLocaleString()}
                        </p>
                      </div>
                      <div className="mt-2.5">
                        <div className="flex items-center justify-between text-[10px] text-zinc-500 mb-0.5">
                          <span>Score</span>
                          <span className="font-mono text-zinc-300">{score ?? "N/A"}</span>
                        </div>
                        <div className="w-full bg-zinc-800/50 rounded-full h-1">
                          <div
                            className={`h-1 rounded-full ${model.color}`}
                            style={{ width: `${score ?? 0}%` }}
                          />
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* SHAP waterfall — GB model */}
          {l.shap_values && (
            <div className="space-y-1.5 pt-1 border-t border-zinc-800">
              <p className="text-xs font-medium text-zinc-400">GB SHAP Breakdown — feature contributions to ${l.shap_values.gb_pred?.toLocaleString() ?? l.shap_values.predicted.toLocaleString()} (GB component)</p>
              <div className="flex items-center justify-between text-xs">
                <span className="text-zinc-500">Average {l.model} price</span>
                <span className="text-zinc-400 font-mono">${l.shap_values.base_value.toLocaleString()}</span>
              </div>
              {(() => {
                const maxDelta = Math.max(...l.shap_values!.features.map(f => Math.abs(f.delta)), 1);
                return l.shap_values!.features.map((f, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <span className="text-zinc-400 w-36 flex-shrink-0 truncate">{f.label}</span>
                    <div className="flex-1 flex items-center gap-1">
                      {f.delta > 0 ? (
                        <>
                          <div className="w-16 flex justify-end">
                            <div className="h-1.5 bg-zinc-800 rounded" style={{ width: "100%" }} />
                          </div>
                          <div className="w-16">
                            <div className="h-1.5 bg-green-500 rounded" style={{ width: `${Math.round((f.delta / maxDelta) * 100)}%` }} />
                          </div>
                        </>
                      ) : (
                        <>
                          <div className="w-16 flex justify-end">
                            <div className="h-1.5 bg-red-500 rounded" style={{ width: `${Math.round((Math.abs(f.delta) / maxDelta) * 100)}%` }} />
                          </div>
                          <div className="w-16">
                            <div className="h-1.5 bg-zinc-800 rounded" style={{ width: "100%" }} />
                          </div>
                        </>
                      )}
                    </div>
                    <span className={`font-mono w-16 text-right ${f.delta > 0 ? "text-green-400" : "text-red-400"}`}>
                      {f.delta > 0 ? "+" : ""}${f.delta.toLocaleString()}
                    </span>
                  </div>
                ));
              })()}
              <div className="flex items-center justify-between text-xs pt-1 border-t border-zinc-800">
                <span className="text-zinc-300 font-medium">Fair value</span>
                <span className="text-white font-mono font-medium">${l.shap_values.predicted.toLocaleString()}</span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="flex flex-wrap gap-2">
        {["new", "active"].includes(l.status) && (
          <>
            <button onClick={() => onAction(l.id, "snooze")} className="px-3 py-1.5 text-xs bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg">Snooze 7d</button>
            <button onClick={() => onAction(l.id, "dismiss")} className="px-3 py-1.5 text-xs bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg">Mark Dismissed</button>
          </>
        )}
        {l.status === "dismissed" && (
          <button onClick={() => onAction(l.id, "undo")} className="px-3 py-1.5 text-xs bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg">Restore</button>
        )}
        {l.listing_url && (
          <a
            href={l.listing_url}
            target="_blank"
            rel="noopener noreferrer"
            className="px-3 py-1.5 text-xs bg-blue-900 hover:bg-blue-800 text-blue-200 rounded-lg text-center font-medium"
          >
            Open Listing ↗
          </a>
        )}
      </div>
    </div>
  );
}
