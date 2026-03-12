import { useState, useEffect, useCallback } from "react";
import { getTracks, getTrackCounts, updateTrack, resolveLinks, buildCart, getLibraryPlaylists } from "../api";
import TrackRow from "./TrackRow";
import CartProgressBanner from "./CartProgressBanner";

const REFRESH_EVENTS = [
  "scan_complete",
  "scan_batch_complete",
  "resolve_complete",
  "file_complete",
  "cart_complete",
];

const CART_EVENTS = [
  "cart_started",
  "cart_progress",
  "cart_track_result",
  "cart_complete",
  "cart_error",
];

export default function TrackQueue({ wsMessage }) {
  const [tracks, setTracks] = useState([]);
  const [counts, setCounts] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(new Set());
  const [search, setSearch] = useState("");
  const [iTunesPlaylists, setITunesPlaylists] = useState([]);
  const [cartState, setCartState] = useState(null);
  const [cartingTrackId, setCartingTrackId] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      setError(null);
      const [trackData, countData] = await Promise.all([
        getTracks(),
        getTrackCounts(),
      ]);
      setTracks(trackData);
      setCounts(countData);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    getLibraryPlaylists()
      .then(setITunesPlaylists)
      .catch((err) => console.warn("Could not load iTunes playlists:", err));
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  /* ---- WebSocket event handling ---- */

  useEffect(() => {
    if (!wsMessage) return;

    if (REFRESH_EVENTS.includes(wsMessage.type)) {
      fetchData();
    }

    if (!CART_EVENTS.includes(wsMessage.type)) return;

    const p = wsMessage.payload || {};

    switch (wsMessage.type) {
      case "cart_started":
        setCartState({ store: p.store, phase: "logging_in", current: 0, total: 0, track: null });
        setCartingTrackId(null);
        break;

      case "cart_progress":
        setCartState((prev) => ({
          ...prev,
          phase: "adding",
          current: p.current - 1,
          total: p.total,
          track: p.track,
        }));
        setCartingTrackId(p.track_id ?? null);
        break;

      case "cart_track_result":
        setCartState((prev) => ({
          ...prev,
          current: p.current,
          total: p.total,
        }));
        if (p.new_status) {
          setTracks((prev) =>
            prev.map((t) =>
              t.id === p.track_id ? { ...t, status: p.new_status } : t,
            ),
          );
        }
        if (p.current === p.total) setCartingTrackId(null);
        break;

      case "cart_complete":
        setCartState({
          store: p.store,
          phase: "done",
          current: p.total,
          total: p.total,
          added: p.added,
          failed: p.failed,
        });
        setCartingTrackId(null);
        break;

      case "cart_error":
        setCartState((prev) => ({
          ...(prev || { store: p.store, current: 0, total: 0 }),
          phase: "error",
          error: p.error,
        }));
        setCartingTrackId(null);
        break;

      default:
        break;
    }
  }, [wsMessage, fetchData]);

  /* ---- Selection helpers ---- */

  const filteredTracks = search
    ? tracks.filter(
        (t) =>
          t.track_name?.toLowerCase().includes(search.toLowerCase()) ||
          t.artist_name?.toLowerCase().includes(search.toLowerCase()),
      )
    : tracks;

  const toggleSelect = (id) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selected.size === filteredTracks.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(filteredTracks.map((t) => t.id)));
    }
  };

  /* ---- Track update from child row ---- */

  const handleTrackUpdate = (updated) => {
    setTracks((prev) =>
      prev.map((t) => (t.id === updated.id ? updated : t)),
    );
    if (["done", "skipped", "baseline"].includes(updated.status)) {
      setTimeout(
        () => setTracks((prev) => prev.filter((t) => t.id !== updated.id)),
        400,
      );
    }
  };

  /* ---- Batch actions ---- */

  const batchUpdateStatus = async (newStatus) => {
    const ids = [...selected];
    if (ids.length === 0) return;
    try {
      await Promise.all(ids.map((id) => updateTrack(id, { status: newStatus })));
      setTracks((prev) =>
        prev.map((t) =>
          ids.includes(t.id) ? { ...t, status: newStatus } : t,
        ),
      );
      if (["done", "skipped"].includes(newStatus)) {
        setTimeout(
          () => setTracks((prev) => prev.filter((t) => !ids.includes(t.id))),
          400,
        );
      }
      setSelected(new Set());
    } catch (err) {
      setError(`Batch update failed: ${err.message}`);
    }
  };

  const handleResolveLinks = async () => {
    try {
      const ids = selected.size > 0 ? [...selected] : undefined;
      await resolveLinks(ids);
    } catch (err) {
      setError(`Resolve failed: ${err.message}`);
    }
  };

  const handleBuildCart = async (store) => {
    try {
      await buildCart(store);
    } catch (err) {
      setError(`Cart build failed: ${err.message}`);
    }
  };

  const openApprovedLinks = (store) => {
    const urlField = store === "beatport" ? "beatport_url" : "traxsource_url";
    tracks
      .filter((t) => t.status === "approved" && t[urlField])
      .forEach((t) => window.open(t[urlField], "_blank", "noopener"));
  };

  const isCartRunning = cartState && (cartState.phase === "logging_in" || cartState.phase === "adding");
  const dismissBanner = () => setCartState(null);

  /* ---- Render ---- */

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-400 animate-pulse">Loading tracks...</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Cart progress banner */}
      {cartState && (
        <div className="relative">
          <CartProgressBanner cartState={cartState} />
          {(cartState.phase === "done" || cartState.phase === "error") && (
            <button
              onClick={dismissBanner}
              className="absolute top-2 right-2 text-gray-500 hover:text-gray-300 text-xs p-1"
              title="Dismiss"
            >
              ✕
            </button>
          )}
        </div>
      )}

      {/* Status summary chips */}
      <div className="flex flex-wrap gap-3 text-xs">
        {Object.entries(counts).map(([status, count]) => (
          <span key={status} className="text-gray-400">
            {status}:{" "}
            <span className="text-gray-200 font-medium">{count}</span>
          </span>
        ))}
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search tracks..."
          className="bg-base-700 border border-base-600 text-gray-200 text-sm rounded
                     px-3 py-1.5 w-64 focus:outline-none focus:border-accent
                     placeholder:text-gray-500"
        />

        <div className="flex flex-wrap gap-2">
          {selected.size > 0 && (
            <>
              <ActionBtn
                color="emerald"
                onClick={() => batchUpdateStatus("approved")}
                label={`Approve (${selected.size})`}
              />
              <ActionBtn
                color="gray"
                onClick={() => batchUpdateStatus("skipped")}
                label={`Skip (${selected.size})`}
              />
            </>
          )}
          <ActionBtn
            color="accent"
            onClick={handleResolveLinks}
            label={`Resolve Links${selected.size ? ` (${selected.size})` : ""}`}
          />
          <ActionBtn
            color="orange"
            onClick={() => openApprovedLinks("beatport")}
            label="Open BP Links"
          />
          <ActionBtn
            color="cyan"
            onClick={() => openApprovedLinks("traxsource")}
            label="Open TS Links"
          />
          <ActionBtn
            color="orange"
            onClick={() => handleBuildCart("beatport")}
            label="Cart BP"
            disabled={isCartRunning}
          />
          <ActionBtn
            color="cyan"
            onClick={() => handleBuildCart("traxsource")}
            label="Cart TS"
            disabled={isCartRunning}
          />
        </div>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-400 text-sm rounded px-4 py-2">
          {error}
        </div>
      )}

      {/* Table */}
      {filteredTracks.length === 0 ? (
        <div className="text-center py-16 text-gray-500 text-sm">
          {search
            ? "No tracks match your search."
            : "No tracks in queue. Scan a playlist to get started."}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-base-600">
          <table className="w-full text-left">
            <thead>
              <tr className="bg-base-700 text-xs text-gray-400 uppercase tracking-wider">
                <th className="px-3 py-2.5 w-10">
                  <input
                    type="checkbox"
                    checked={
                      selected.size === filteredTracks.length &&
                      filteredTracks.length > 0
                    }
                    onChange={toggleSelectAll}
                    className="accent-accent"
                  />
                </th>
                <th className="px-1.5 py-2.5 w-9"></th>
                <th className="px-3 py-2.5">Track</th>
                <th className="px-3 py-2.5">Playlist</th>
                <th className="px-3 py-2.5">Links</th>
                <th className="px-3 py-2.5">Match</th>
                <th className="px-3 py-2.5">Playlists</th>
                <th className="px-3 py-2.5">Status</th>
                <th className="px-3 py-2.5">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredTracks.map((track) => (
                <TrackRow
                  key={track.id}
                  track={track}
                  selected={selected.has(track.id)}
                  onToggle={toggleSelect}
                  onTrackUpdate={handleTrackUpdate}
                  allPlaylists={iTunesPlaylists}
                  isCarting={track.id === cartingTrackId}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* Small helper so the toolbar buttons stay DRY */
const COLOR_MAP = {
  emerald: "bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 border-emerald-500/30",
  gray: "bg-gray-500/20 text-gray-400 hover:bg-gray-500/30 border-gray-500/30",
  accent: "bg-accent/20 text-accent hover:bg-accent/30 border-accent/30",
  orange: "bg-orange-500/20 text-orange-400 hover:bg-orange-500/30 border-orange-500/30",
  cyan: "bg-cyan-500/20 text-cyan-400 hover:bg-cyan-500/30 border-cyan-500/30",
};

function ActionBtn({ color, onClick, label, disabled = false }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`text-xs px-3 py-1.5 rounded border transition-colors
        ${COLOR_MAP[color] || COLOR_MAP.gray}
        ${disabled ? "opacity-40 cursor-not-allowed" : ""}`}
    >
      {label}
    </button>
  );
}
