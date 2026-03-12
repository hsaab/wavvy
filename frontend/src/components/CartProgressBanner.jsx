import { useMemo } from "react";

const STORE_LABELS = { beatport: "Beatport", traxsource: "Traxsource" };
const STORE_COLORS = {
  beatport: { bar: "bg-orange-500", text: "text-orange-400", bg: "bg-orange-500/10", border: "border-orange-500/20" },
  traxsource: { bar: "bg-cyan-500", text: "text-cyan-400", bg: "bg-cyan-500/10", border: "border-cyan-500/20" },
};

export default function CartProgressBanner({ cartState }) {
  const { store, phase, current, total, track, error } = cartState;

  const colors = STORE_COLORS[store] || STORE_COLORS.beatport;
  const storeLabel = STORE_LABELS[store] || store;
  const pct = useMemo(() => (total > 0 ? Math.round((current / total) * 100) : 0), [current, total]);

  if (phase === "error") {
    return (
      <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 flex items-center gap-3">
        <span className="text-red-400 text-sm font-medium shrink-0">
          {storeLabel} cart failed
        </span>
        <span className="text-red-300/80 text-xs truncate">{error}</span>
      </div>
    );
  }

  if (phase === "done") {
    const isEmpty = total === 0 && (cartState.added ?? 0) === 0;
    return (
      <div className={`rounded-lg border ${isEmpty ? "border-yellow-500/30 bg-yellow-500/10" : `${colors.border} ${colors.bg}`} px-4 py-3 flex items-center gap-3`}>
        <span className={`${isEmpty ? "text-yellow-400" : colors.text} text-sm font-medium`}>
          {isEmpty ? `No eligible tracks for ${storeLabel}` : `${storeLabel} cart complete`}
        </span>
        <span className="text-gray-400 text-xs">
          {isEmpty
            ? "Approve tracks and resolve links first"
            : `${cartState.added} added${cartState.failed > 0 ? `, ${cartState.failed} failed` : ""}`}
        </span>
      </div>
    );
  }

  return (
    <div className={`rounded-lg border ${colors.border} ${colors.bg} px-4 py-3 space-y-2`}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <Spinner className={colors.text} />
          <span className={`${colors.text} text-sm font-medium shrink-0`}>
            {phase === "logging_in" ? `Logging in to ${storeLabel}…` : `Adding to ${storeLabel} cart`}
          </span>
          {phase === "adding" && track && (
            <span className="text-gray-400 text-xs truncate">{track}</span>
          )}
        </div>
        {phase === "adding" && total > 0 && (
          <span className="text-gray-400 text-xs font-mono shrink-0">
            {current}/{total}
          </span>
        )}
      </div>

      {phase === "adding" && total > 0 && (
        <div className="w-full h-1 rounded-full bg-base-600 overflow-hidden">
          <div
            className={`h-full rounded-full ${colors.bar} transition-all duration-500 ease-out`}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}

function Spinner({ className = "" }) {
  return (
    <svg
      className={`animate-spin h-3.5 w-3.5 ${className}`}
      viewBox="0 0 24 24"
      fill="none"
    >
      <circle
        className="opacity-25"
        cx="12" cy="12" r="10"
        stroke="currentColor" strokeWidth="3"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8v3a5 5 0 00-5 5H4z"
      />
    </svg>
  );
}
