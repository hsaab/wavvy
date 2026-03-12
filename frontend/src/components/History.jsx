import { useState, useEffect, useCallback } from "react";
import { getTracks } from "../api";
import StatusBadge from "./StatusBadge";
import ConfidenceBadge from "./ConfidenceBadge";

const FILTERS = [
  { key: "done", label: "Completed" },
  { key: "baseline", label: "Baseline" },
];

export default function History({ wsMessage }) {
  const [tracks, setTracks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("done");

  const fetchData = useCallback(async () => {
    try {
      setError(null);
      const data = await getTracks(filter);
      setTracks(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    setLoading(true);
    fetchData();
  }, [fetchData]);

  useEffect(() => {
    if (wsMessage?.type === "file_complete") fetchData();
  }, [wsMessage, fetchData]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-400 animate-pulse">Loading...</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <h2 className="text-sm font-medium text-gray-300">
          History{" "}
          <span className="text-gray-500">({tracks.length})</span>
        </h2>
        <div className="flex gap-1">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`text-xs px-3 py-1 rounded transition-colors ${
                filter === f.key
                  ? "bg-accent/20 text-accent border border-accent/30"
                  : "text-gray-400 hover:text-gray-200 border border-base-600"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-400 text-sm rounded px-4 py-2">
          {error}
        </div>
      )}

      {tracks.length === 0 ? (
        <div className="text-center py-16 text-gray-500 text-sm">
          No tracks in this category.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-base-600">
          <table className="w-full text-left">
            <thead>
              <tr className="bg-base-700 text-xs text-gray-400 uppercase tracking-wider">
                <th className="px-3 py-2.5">Track</th>
                <th className="px-3 py-2.5">Playlist</th>
                <th className="px-3 py-2.5">Genre</th>
                <th className="px-3 py-2.5">Match</th>
                <th className="px-3 py-2.5">Status</th>
                <th className="px-3 py-2.5">Date</th>
              </tr>
            </thead>
            <tbody>
              {tracks.map((track) => (
                <tr
                  key={track.id}
                  className="border-b border-base-600 hover:bg-base-700/50 transition-colors"
                >
                  <td className="px-3 py-2">
                    <div className="text-sm text-gray-100 font-medium">
                      {track.track_name}
                    </div>
                    <div className="text-xs text-gray-400">
                      {track.artist_name}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-xs text-gray-400">
                    {track.source_playlist}
                  </td>
                  <td className="px-3 py-2 text-xs text-gray-300">
                    {track.genre || "--"}
                  </td>
                  <td className="px-3 py-2">
                    <ConfidenceBadge
                      confidence={track.match_confidence}
                      score={track.confidence_score}
                    />
                  </td>
                  <td className="px-3 py-2">
                    <StatusBadge status={track.status} />
                  </td>
                  <td className="px-3 py-2 text-xs text-gray-400">
                    {new Date(track.date_detected).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
