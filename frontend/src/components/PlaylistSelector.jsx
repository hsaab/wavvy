import { useState, useEffect } from "react";
import { getPlaylists, triggerScan, getSpotifyStatus, getSpotifyAuthUrl } from "../api";

export default function PlaylistSelector({ wsMessage, onClose }) {
  const [playlists, setPlaylists] = useState([]);
  const [selected, setSelected] = useState(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [scanProgress, setScanProgress] = useState(null);
  const [spotifyAuthed, setSpotifyAuthed] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const { authenticated } = await getSpotifyStatus();
        setSpotifyAuthed(authenticated);
        if (authenticated) {
          const data = await getPlaylists();
          setPlaylists(data);
        }
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  useEffect(() => {
    if (!wsMessage) return;
    // #region agent log
    if (wsMessage.type && wsMessage.type.startsWith('scan')) {
      fetch('http://127.0.0.1:7458/ingest/b530fd28-deaa-4c3d-9cd6-e49423133f3b',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'5d0c12'},body:JSON.stringify({sessionId:'5d0c12',location:'PlaylistSelector.jsx:wsEffect',message:'WS event in PlaylistSelector',data:{type:wsMessage.type,payload:wsMessage.payload},timestamp:Date.now(),hypothesisId:'H1'})}).catch(()=>{});
    }
    // #endregion
    if (wsMessage.type === "scan_batch_progress") {
      setScanProgress(wsMessage.payload);
    }
    if (wsMessage.type === "scan_batch_complete") {
      setScanning(false);
      setScanProgress(null);
      if (onClose) setTimeout(onClose, 800);
    }
  }, [wsMessage, onClose]);

  const togglePlaylist = (id) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const handleScan = async () => {
    if (selected.size === 0) return;
    setScanning(true);
    setError(null);
    // #region agent log
    const _selectedIds = [...selected];
    fetch('http://127.0.0.1:7458/ingest/b530fd28-deaa-4c3d-9cd6-e49423133f3b',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'5d0c12'},body:JSON.stringify({sessionId:'5d0c12',location:'PlaylistSelector.jsx:handleScan',message:'triggerScan called',data:{selectedCount:_selectedIds.length,ids:_selectedIds},timestamp:Date.now(),hypothesisId:'H2'})}).catch(()=>{});
    // #endregion
    try {
      const result = await triggerScan([...selected]);
      // #region agent log
      fetch('http://127.0.0.1:7458/ingest/b530fd28-deaa-4c3d-9cd6-e49423133f3b',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'5d0c12'},body:JSON.stringify({sessionId:'5d0c12',location:'PlaylistSelector.jsx:handleScan',message:'triggerScan response OK',data:{result},timestamp:Date.now(),hypothesisId:'H2'})}).catch(()=>{});
      // #endregion
    } catch (err) {
      // #region agent log
      fetch('http://127.0.0.1:7458/ingest/b530fd28-deaa-4c3d-9cd6-e49423133f3b',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'5d0c12'},body:JSON.stringify({sessionId:'5d0c12',location:'PlaylistSelector.jsx:handleScan',message:'triggerScan FAILED',data:{error:err.message},timestamp:Date.now(),hypothesisId:'H2'})}).catch(()=>{});
      // #endregion
      setError(err.message);
      setScanning(false);
    }
  };

  const handleConnectSpotify = async () => {
    try {
      const { url } = await getSpotifyAuthUrl();
      window.open(url, "_blank", "noopener");
    } catch (err) {
      setError(err.message);
    }
  };

  const handleRefreshAuth = async () => {
    setLoading(true);
    try {
      const { authenticated } = await getSpotifyStatus();
      setSpotifyAuthed(authenticated);
      if (authenticated) {
        const data = await getPlaylists();
        setPlaylists(data);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  /* ---- Spotify not connected ---- */
  if (!loading && spotifyAuthed === false) {
    return (
      <ModalShell onClose={onClose}>
        <div className="space-y-4 text-center py-8">
          <p className="text-gray-400 text-sm">
            Spotify is not connected. Authorize to scan playlists.
          </p>
          <div className="flex justify-center gap-3">
            <button
              onClick={handleConnectSpotify}
              className="px-4 py-2 rounded bg-green-600 text-white text-sm
                         hover:bg-green-500 transition-colors"
            >
              Connect Spotify
            </button>
            <button
              onClick={handleRefreshAuth}
              className="px-4 py-2 rounded border border-base-600 text-gray-300 text-sm
                         hover:border-gray-500 transition-colors"
            >
              Refresh Status
            </button>
          </div>
          {error && (
            <p className="text-red-400 text-xs mt-2">{error}</p>
          )}
        </div>
      </ModalShell>
    );
  }

  return (
    <ModalShell onClose={onClose}>
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium text-gray-200">
            Select Playlists to Scan
          </h2>
          <button
            onClick={handleScan}
            disabled={selected.size === 0 || scanning}
            className="text-sm px-4 py-1.5 rounded bg-accent text-white
                       hover:bg-accent-hover disabled:opacity-40
                       disabled:cursor-not-allowed transition-colors"
          >
            {scanning
              ? scanProgress
                ? `Scanning ${scanProgress.current}/${scanProgress.total}...`
                : "Scanning..."
              : `Scan Selected (${selected.size})`}
          </button>
        </div>

        {error && (
          <div className="bg-red-500/10 border border-red-500/30 text-red-400 text-xs rounded px-3 py-2">
            {error}
          </div>
        )}

        {loading ? (
          <div className="text-gray-400 text-sm py-8 text-center animate-pulse">
            Loading playlists...
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2 max-h-[50vh] overflow-y-auto pr-1">
            {playlists.map((pl) => (
              <button
                key={pl.id}
                onClick={() => togglePlaylist(pl.id)}
                className={`flex items-center gap-3 p-3 rounded-lg border transition-colors text-left ${
                  selected.has(pl.id)
                    ? "border-accent bg-accent/10"
                    : "border-base-600 bg-base-800 hover:border-base-500"
                }`}
              >
                {pl.image && (
                  <img
                    src={pl.image}
                    alt=""
                    className="w-10 h-10 rounded object-cover flex-shrink-0"
                  />
                )}
                <div className="min-w-0">
                  <div className="text-sm text-gray-200 font-medium truncate">
                    {pl.name}
                  </div>
                  <div className="text-xs text-gray-500">
                    {pl.track_count} tracks
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </ModalShell>
  );
}

function ModalShell({ children, onClose }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />
      <div className="relative bg-base-800 border border-base-600 rounded-xl shadow-2xl w-full max-w-2xl mx-4 p-6">
        <button
          onClick={onClose}
          className="absolute top-3 right-3 text-gray-500 hover:text-gray-300 transition-colors text-lg leading-none"
        >
          &times;
        </button>
        {children}
      </div>
    </div>
  );
}
