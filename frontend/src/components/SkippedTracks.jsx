import { useState, useEffect, useCallback } from "react";
import { getTracks, updateTrack } from "../api";
import StatusBadge from "./StatusBadge";

export default function SkippedTracks({ wsMessage }) {
  const [tracks, setTracks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      setError(null);
      const data = await getTracks("skipped");
      setTracks(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useEffect(() => {
    if (!wsMessage) return;
    if (
      wsMessage.type === "scan_complete" ||
      wsMessage.type === "scan_batch_complete"
    ) {
      fetchData();
    }
  }, [wsMessage, fetchData]);

  const handleUnskip = async (track) => {
    try {
      await updateTrack(track.id, { status: "new" });
      setTracks((prev) => prev.filter((t) => t.id !== track.id));
    } catch (err) {
      setError(`Failed to unskip: ${err.message}`);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-400 animate-pulse">Loading...</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <h2 className="text-sm font-medium text-gray-300">
        Skipped Tracks{" "}
        <span className="text-gray-500">({tracks.length})</span>
      </h2>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-400 text-sm rounded px-4 py-2">
          {error}
        </div>
      )}

      {tracks.length === 0 ? (
        <div className="text-center py-16 text-gray-500 text-sm">
          No skipped tracks.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-base-600">
          <table className="w-full text-left">
            <thead>
              <tr className="bg-base-700 text-xs text-gray-400 uppercase tracking-wider">
                <th className="px-3 py-2.5">Track</th>
                <th className="px-3 py-2.5">Playlist</th>
                <th className="px-3 py-2.5">Status</th>
                <th className="px-3 py-2.5">Action</th>
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
                  <td className="px-3 py-2">
                    <StatusBadge status={track.status} />
                  </td>
                  <td className="px-3 py-2">
                    <button
                      onClick={() => handleUnskip(track)}
                      className="text-xs px-2 py-0.5 rounded bg-blue-500/20 text-blue-400
                                 hover:bg-blue-500/30 transition-colors"
                    >
                      Un-skip
                    </button>
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
