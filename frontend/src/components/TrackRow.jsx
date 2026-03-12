import { useState } from "react";
import StatusBadge from "./StatusBadge";
import ConfidenceBadge from "./ConfidenceBadge";
import PlaylistPicker from "./PlaylistPicker";
import PlayButton from "./PlayButton";
import { updateTrack, addTrackToPlaylists } from "../api";

export default function TrackRow({ track, selected, onToggle, onTrackUpdate, allPlaylists, isCarting = false }) {
  const [busy, setBusy] = useState(false);
  const [syncing, setSyncing] = useState(false);

  const patchTrack = async (fields) => {
    setBusy(true);
    try {
      const updated = await updateTrack(track.id, fields);
      onTrackUpdate(updated);
    } catch (err) {
      console.error("Failed to update track:", err);
    } finally {
      setBusy(false);
    }
  };

  const openLink = (url) => {
    if (url) window.open(url, "_blank", "noopener");
  };

  const isLocked = track.status === "done" || track.status === "baseline";

  return (
    <tr className={`border-b border-base-600 transition-colors ${
      isCarting
        ? "bg-orange-500/5 ring-1 ring-inset ring-orange-500/20"
        : "hover:bg-base-700/50"
    }`}>
      {/* Checkbox */}
      <td className="px-3 py-2">
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggle(track.id)}
          className="accent-accent"
        />
      </td>

      {/* Play */}
      <td className="px-1.5 py-2">
        <PlayButton
          spotifyId={track.spotify_id}
          trackName={track.track_name}
          artistName={track.artist_name}
        />
      </td>

      {/* Track + Artist */}
      <td className="px-3 py-2">
        <div
          className="text-sm text-gray-100 font-medium truncate max-w-[220px]"
          title={track.track_name}
        >
          {track.track_name}
        </div>
        <div
          className="text-xs text-gray-400 truncate max-w-[220px]"
          title={track.artist_name}
        >
          {track.artist_name}
        </div>
      </td>

      {/* Source playlist */}
      <td
        className="px-3 py-2 text-xs text-gray-400 truncate max-w-[130px]"
        title={track.source_playlist}
      >
        {track.source_playlist}
      </td>

      {/* Store links */}
      <td className="px-3 py-2">
        <div className="flex gap-1.5">
          {track.beatport_url ? (
            <button
              onClick={() => openLink(track.beatport_url)}
              className="text-xs px-2 py-0.5 rounded bg-orange-500/20 text-orange-400
                         hover:bg-orange-500/30 transition-colors"
            >
              BP
            </button>
          ) : (
            <span className="text-xs text-gray-600">--</span>
          )}
          {track.traxsource_url ? (
            <button
              onClick={() => openLink(track.traxsource_url)}
              className="text-xs px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-400
                         hover:bg-cyan-500/30 transition-colors"
            >
              TS
            </button>
          ) : (
            <span className="text-xs text-gray-600">--</span>
          )}
        </div>
      </td>

      {/* Confidence */}
      <td className="px-3 py-2">
        <ConfidenceBadge
          confidence={track.match_confidence}
          score={track.confidence_score}
        />
      </td>

      {/* Target Playlists */}
      <td className="px-3 py-2">
        <div className="flex items-center gap-1">
          <PlaylistPicker
            allPlaylists={allPlaylists}
            selected={track.target_playlists || []}
            onChange={(playlists) => patchTrack({ target_playlists: playlists })}
            disabled={busy}
          />
          {track.status === "done" && (track.target_playlists || []).length > 0 && (
            <button
              disabled={syncing}
              onClick={async () => {
                setSyncing(true);
                try {
                  await addTrackToPlaylists(track.id);
                } catch (err) {
                  console.error("Failed to sync playlists:", err);
                } finally {
                  setSyncing(false);
                }
              }}
              title="Add to playlists in Apple Music now"
              className="text-xs px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-400
                         hover:bg-purple-500/30 disabled:opacity-40 transition-colors"
            >
              {syncing ? "..." : "Sync"}
            </button>
          )}
        </div>
      </td>

      {/* Status */}
      <td className="px-3 py-2">
        {isCarting ? (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-medium rounded border bg-orange-500/20 text-orange-400 border-orange-500/30">
            <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v3a5 5 0 00-5 5H4z" />
            </svg>
            Carting…
          </span>
        ) : (
          <StatusBadge status={track.status} />
        )}
      </td>

      {/* Actions */}
      <td className="px-3 py-2">
        <div className="flex gap-1">
          {track.status === "new" && (
            <>
              <button
                disabled={busy}
                onClick={() => patchTrack({ status: "approved" })}
                className="text-xs px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400
                           hover:bg-emerald-500/30 disabled:opacity-40 transition-colors"
              >
                Approve
              </button>
              <button
                disabled={busy}
                onClick={() => patchTrack({ status: "skipped" })}
                className="text-xs px-2 py-0.5 rounded bg-gray-500/20 text-gray-400
                           hover:bg-gray-500/30 disabled:opacity-40 transition-colors"
              >
                Skip
              </button>
            </>
          )}
          {track.status === "approved" && (
            <button
              disabled={busy}
              onClick={() => patchTrack({ status: "new" })}
              className="text-xs px-2 py-0.5 rounded bg-blue-500/20 text-blue-400
                         hover:bg-blue-500/30 disabled:opacity-40 transition-colors"
            >
              Unapprove
            </button>
          )}
          {track.status === "cart_failed" && (
            <button
              disabled={busy}
              onClick={() => patchTrack({ status: "approved" })}
              className="text-xs px-2 py-0.5 rounded bg-amber-500/20 text-amber-400
                         hover:bg-amber-500/30 disabled:opacity-40 transition-colors"
            >
              Retry
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}
