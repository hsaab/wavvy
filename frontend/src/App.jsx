import { useState, useEffect, useCallback, useRef } from "react";
import useWebSocket from "./hooks/useWebSocket";
import { getHealth, scanLibrary, getConfig, getSpotifyStatus, getPlaylists, triggerScan } from "./api";
import { PlayerProvider } from "./context/PlayerContext";
import TrackQueue from "./components/TrackQueue";
import SkippedTracks from "./components/SkippedTracks";
import History from "./components/History";
import Settings from "./components/Settings";
import PlaylistSelector from "./components/PlaylistSelector";
import PlayerBar from "./components/PlayerBar";
import Toast from "./components/Toast";

const TABS = [
  { id: "queue", label: "Queue" },
  { id: "skipped", label: "Skipped" },
  { id: "history", label: "History" },
  { id: "settings", label: "Settings" },
];

const TOAST_EVENTS = {
  scan_batch_complete: (p) =>
    `Scan done — ${p.results?.reduce((s, r) => s + (r.new || 0), 0) || 0} new track(s)`,
  cart_complete: (p) =>
    `${p.store} cart ready — ${p.added} added${p.failed ? `, ${p.failed} failed` : ""}`,
  cart_error: (p) => `Cart error (${p.store}): ${p.error}`,
  file_complete: (p) => `"${p.track_name}" processed`,
  file_unmatched: (p) => `Unmatched file: ${p.filename}`,
  file_error: (p) => `File error: ${p.error}`,
};

export default function App() {
  const [activeTab, setActiveTab] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    return params.has("spotify") ? "settings" : "queue";
  });
  const [showScanModal, setShowScanModal] = useState(false);
  const [health, setHealth] = useState(null);
  const [toasts, setToasts] = useState([]);
  const [refreshingLibrary, setRefreshingLibrary] = useState(false);
  const { connected, lastMessage } = useWebSocket();

  const addToast = useCallback((message, variant = "info") => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, message, variant }]);
  }, []);

  const dismissToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  /* Surface backend WebSocket events as toasts */
  useEffect(() => {
    if (!lastMessage) return;
    const builder = TOAST_EVENTS[lastMessage.type];
    if (builder) {
      const variant = lastMessage.type.includes("error") || lastMessage.type === "file_unmatched"
        ? "error"
        : "success";
      addToast(builder(lastMessage.payload || {}), variant);
    }
  }, [lastMessage, addToast]);

  /* Poll health every 30 s */
  useEffect(() => {
    const fetchHealth = async () => {
      try {
        setHealth(await getHealth());
      } catch {
        setHealth(null);
      }
    };
    fetchHealth();
    const id = setInterval(fetchHealth, 30_000);
    return () => clearInterval(id);
  }, []);

  /* Auto-scan configured playlists once per session */
  const autoScannedRef = useRef(false);
  useEffect(() => {
    if (autoScannedRef.current) return;
    autoScannedRef.current = true;

    (async () => {
      try {
        const config = await getConfig();
        const names = config.auto_scan_playlists;
        if (!names || names.length === 0) return;

        const { authenticated } = await getSpotifyStatus();
        if (!authenticated) return;

        const playlists = await getPlaylists();
        const lowerNames = new Set(names.map((n) => n.toLowerCase()));
        const matched = playlists.filter((pl) =>
          lowerNames.has(pl.name.toLowerCase())
        );
        if (matched.length === 0) return;

        addToast(`Auto-scanning ${matched.length} playlist(s)...`, "info");
        await triggerScan(matched.map((pl) => pl.id));
      } catch {
        /* auto-scan is best-effort; failures are silent */
      }
    })();
  }, [addToast]);

  const handleRefreshLibrary = async () => {
    setRefreshingLibrary(true);
    try {
      const result = await scanLibrary();
      addToast(`iTunes library refreshed — ${result.track_count} tracks`, "success");
    } catch (err) {
      addToast(`Library scan failed: ${err.message}`, "error");
    } finally {
      setRefreshingLibrary(false);
    }
  };

  return (
    <PlayerProvider>
    <div className="min-h-screen flex flex-col bg-base-900 text-gray-100">
      {/* Header */}
      <header className="bg-base-800 border-b border-base-600 px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Logo />
          <h1 className="text-lg font-semibold tracking-wide text-gray-100">
            Wavvy
          </h1>
        </div>

        <div className="flex items-center gap-4 text-xs">
          <DriveIndicator health={health} />
          <StatusDot
            ok={health?.supabase}
            label={health?.supabase ? "DB" : "DB"}
          />
          <StatusDot ok={connected} label="WS" />

          <button
            onClick={handleRefreshLibrary}
            disabled={refreshingLibrary}
            className="px-2.5 py-1.5 rounded text-xs font-medium transition-colors
                       border border-base-600 text-gray-400 hover:text-gray-200
                       hover:border-gray-500 disabled:opacity-50"
            title="Refresh iTunes Library Cache"
          >
            {refreshingLibrary ? "Scanning..." : "Refresh Library"}
          </button>

          <button
            onClick={() => setShowScanModal(true)}
            className="px-3 py-1.5 rounded bg-accent text-white text-xs font-medium
                       hover:bg-accent-hover transition-colors"
          >
            Scan Playlists
          </button>
        </div>
      </header>

      {/* Tab bar */}
      <nav className="bg-base-800 px-6 flex gap-1 border-b border-base-600">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2.5 text-sm font-medium transition-colors relative ${
              activeTab === tab.id
                ? "text-accent"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            {tab.label}
            {activeTab === tab.id && (
              <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-accent" />
            )}
          </button>
        ))}
      </nav>

      {/* Content — pb-20 leaves room for the fixed PlayerBar */}
      <main className="flex-1 p-6 pb-20 overflow-y-auto">
        {activeTab === "queue" && <TrackQueue wsMessage={lastMessage} />}
        {activeTab === "skipped" && <SkippedTracks wsMessage={lastMessage} />}
        {activeTab === "history" && <History wsMessage={lastMessage} />}
        {activeTab === "settings" && <Settings />}
      </main>

      {/* Scan modal overlay */}
      {showScanModal && (
        <PlaylistSelector
          wsMessage={lastMessage}
          onClose={() => setShowScanModal(false)}
        />
      )}

      {/* Player bar (fixed at bottom) */}
      <PlayerBar />

      {/* Toast stack */}
      <Toast toasts={toasts} onDismiss={dismissToast} />
    </div>
    </PlayerProvider>
  );
}

function StatusDot({ ok, label }) {
  return (
    <span className="flex items-center gap-1.5 text-gray-400" title={label}>
      <span
        className={`inline-block w-1.5 h-1.5 rounded-full ${
          ok === true
            ? "bg-green-400"
            : ok === false
              ? "bg-red-400"
              : "bg-gray-600"
        }`}
      />
      {label}
    </span>
  );
}

function Logo() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 64 64"
      fill="none"
      className="w-8 h-8 shrink-0"
    >
      <circle cx="32" cy="32" r="30" stroke="#4f8fff" strokeWidth="2.5" fill="#0f0f1a" />
      <circle cx="32" cy="32" r="24" stroke="#1e2a47" strokeWidth="1" fill="none" />
      <circle cx="32" cy="32" r="20" stroke="#1e2a47" strokeWidth="0.75" fill="none" />
      <circle cx="32" cy="32" r="16" stroke="#1e2a47" strokeWidth="0.75" fill="none" />
      <circle cx="32" cy="32" r="10" fill="#1a1a2e" stroke="#4f8fff" strokeWidth="1.5" />
      <circle cx="32" cy="32" r="3" fill="#4f8fff" />
      <g opacity="0.6">
        <rect x="42" y="22" width="2.5" height="20" rx="1.25" fill="#4f8fff" />
        <rect x="47" y="18" width="2.5" height="28" rx="1.25" fill="#4f8fff" />
        <rect x="52" y="24" width="2.5" height="16" rx="1.25" fill="#4f8fff" />
      </g>
    </svg>
  );
}

function DriveIndicator({ health }) {
  const mounted = health?.drive_mounted;
  const path = health?.drive_path || "/Volumes/My Passport";

  return (
    <span
      className={`flex items-center gap-1.5 px-2 py-0.5 rounded text-xs
        ${mounted
          ? "bg-green-500/10 text-green-400 border border-green-500/20"
          : "bg-red-500/10 text-red-400 border border-red-500/20"
        }`}
      title={mounted ? `Drive mounted at ${path}` : `Drive not mounted: ${path}`}
    >
      <span
        className={`inline-block w-1.5 h-1.5 rounded-full ${
          mounted ? "bg-green-400" : "bg-red-400 animate-pulse"
        }`}
      />
      {mounted ? "Drive" : "No Drive"}
    </span>
  );
}
