"use client";

import type { Listing } from "@/lib/types";
import { getDrivetrain, getPowertrain, getDealCategory, getDealCategoryStyle } from "@/lib/vehicle-tags";

function timeAgo(dateStr: string) {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

interface Props {
  listing: Listing;
  selected: boolean;
  onSelect: () => void;
  onAction: (id: string, action: string, value?: unknown) => void;
  tab: string;
}

export default function ListingCard({ listing: l, selected, onSelect, onAction, tab }: Props) {
  const isActive = tab === "active";

  return (
    <div
      onClick={onSelect}
      className={`rounded-xl border p-4 cursor-pointer transition-all ${
        selected
          ? "border-blue-500 bg-zinc-800"
          : "border-zinc-800 bg-zinc-900 hover:border-zinc-700"
      }`}
    >
      <div className="flex items-start justify-between gap-4">
        {/* Photo */}
        {l.photos_url?.[0] ? (
          <img
            src={l.photos_url[0]}
            alt={`${l.year} ${l.make} ${l.model}`}
            className="w-24 h-16 object-cover rounded-lg flex-shrink-0"
          />
        ) : (
          <div className="w-24 h-16 bg-zinc-800 rounded-lg flex-shrink-0 flex items-center justify-center text-zinc-600 text-xs">
            No photo
          </div>
        )}

        {/* Main info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-white">
              {l.year} {l.make} {l.model}
            </span>
            {l.condition === "new" && (
              <span className="px-1.5 py-0.5 text-xs bg-emerald-900 text-emerald-300 rounded font-medium">NEW CAR</span>
            )}
            {l.status === "new" && (
              <span className="px-1.5 py-0.5 text-xs bg-blue-900 text-blue-300 rounded">JUST LISTED</span>
            )}
            {l.condition !== "new" && l.accident_count && l.accident_count > 0 ? (
              <span className="px-1.5 py-0.5 text-xs bg-orange-900 text-orange-300 rounded">
                {l.accident_count} accident{l.accident_count > 1 ? "s" : ""}
              </span>
            ) : null}
          </div>

          {/* Trim + drivetrain + powertrain */}
          {(l.trim || getDrivetrain(l.trim, l.model, l.drivetrain) || getPowertrain(l.trim, l.model)) && (
            <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
              {l.trim && (
                <span className="text-xs text-zinc-400">{l.trim}</span>
              )}
              {(() => {
                const dt = getDrivetrain(l.trim, l.model, l.drivetrain);
                const pt = getPowertrain(l.trim, l.model);
                return (
                  <>
                    {dt && (
                      <span className={`px-1.5 py-0.5 text-xs rounded font-medium ${dt === "AWD" ? "bg-blue-900/60 text-blue-300" : "bg-zinc-700 text-zinc-400"}`}>
                        {dt}
                      </span>
                    )}
                    {pt && (
                      <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-green-900/60 text-green-300">
                        {pt}
                      </span>
                    )}
                  </>
                );
              })()}
            </div>
          )}

          <div className="flex items-center gap-3 mt-1 text-sm text-zinc-400">
            <span>${l.price?.toLocaleString()}</span>
            {l.condition === "new"
              ? <span className="text-emerald-600">0 mi (new)</span>
              : l.mileage ? <span>{l.mileage.toLocaleString()} mi</span> : null
            }
            {l.condition !== "new" && l.fair_price && (
              <span className={l.price < l.fair_price ? "text-green-600" : "text-zinc-500"}>
                {l.price < l.fair_price
                  ? `$${(l.fair_price - l.price).toLocaleString()} below market`
                  : `$${(l.price - l.fair_price).toLocaleString()} above market`}
              </span>
            )}
            <span className="text-zinc-600">{{
              cargurus: "CarGurus", carscom: "Cars.com", dealer: "Dealer",
            }[l.source] ?? l.source.replace("_new", "")}</span>
            <span className="text-zinc-500 font-mono text-xs select-all" title="Listing ID">#{l.id.slice(0, 8)}</span>
          </div>

          {/* Insight tags */}
          {l.insights && (l.insights.cpo || (l.insights.packages && l.insights.packages.length > 0) || l.insights.price_drop || l.insights.owner_notes) && (
            <div className="flex items-center gap-1 mt-1.5 flex-wrap">
              {l.insights.cpo && (
                <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-violet-900/70 text-violet-300 border border-violet-700">CPO</span>
              )}
              {l.insights.price_drop && (
                <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-amber-900/70 text-amber-300 border border-amber-700">
                  ${l.insights.price_drop.toLocaleString()} drop
                </span>
              )}
              {(l.insights.packages || []).slice(0, 2).map((pkg) => (
                <span key={pkg} className="px-1.5 py-0.5 text-xs rounded bg-zinc-700/80 text-zinc-300">
                  {pkg}
                </span>
              ))}
              {l.insights.owner_notes && (
                <span className="text-xs text-zinc-500 italic">{l.insights.owner_notes}</span>
              )}
            </div>
          )}
        </div>

        {/* Deal category + days */}
        <div className="flex flex-col items-end gap-2 flex-shrink-0">
          {getDealCategory(l.value_score) ? (
            <span className={`px-2.5 py-1 text-xs font-semibold rounded-lg whitespace-nowrap ${getDealCategoryStyle(l.value_score)}`}>
              {getDealCategory(l.value_score)}
            </span>
          ) : (
            <span className="px-2.5 py-1 text-xs font-semibold rounded-lg bg-zinc-800 text-zinc-500 border border-zinc-700">
              Unscored
            </span>
          )}
          <span className="text-xs text-zinc-600">{timeAgo(l.first_seen_at)}</span>
        </div>
      </div>

      {/* Actions */}
      {isActive && (
        <div className="flex gap-2 mt-3" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={() => onAction(l.id, "snooze")}
            className="px-3 py-1 text-xs bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg transition-colors"
          >
            Snooze 7d
          </button>
        </div>
      )}
      {tab === "dismissed" && (
        <div className="mt-3" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={() => onAction(l.id, "undo")}
            className="px-3 py-1 text-xs bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg transition-colors"
          >
            Restore
          </button>
        </div>
      )}
    </div>
  );
}

