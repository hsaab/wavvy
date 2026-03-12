import { useState, useEffect, useCallback } from "react";
import { getConfig, updateConfig, getHealth, scanLibrary, getLibraryStatus, getSpotifyStatus, getSpotifyAuthUrl } from "../api";

const INPUT_CLASS =
  "w-full bg-base-700 border border-base-600 text-gray-200 text-sm rounded " +
  "px-3 py-2 focus:outline-none focus:border-accent placeholder:text-gray-500";

const LABEL_CLASS = "block text-xs font-medium text-gray-400 mb-1";

const SECTION_CLASS =
  "bg-base-800 border border-base-600 rounded-lg p-5 space-y-4";

const GENRES = ["Disco+Melodic", "House", "Worldtech", "Tech House"];

export default function Settings() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);
  const [health, setHealth] = useState(null);
  const [libraryStatus, setLibraryStatus] = useState(null);
  const [libraryScanning, setLibraryScanning] = useState(false);
  const [spotifyAuth, setSpotifyAuth] = useState(null);
  const [spotifyConnecting, setSpotifyConnecting] = useState(false);

  const fetchConfig = useCallback(async () => {
    try {
      setError(null);
      const [cfg, h, lib, spotStatus] = await Promise.all([
        getConfig(),
        getHealth(),
        getLibraryStatus(),
        getSpotifyStatus().catch(() => ({ authenticated: false })),
      ]);
      setConfig(cfg);
      setHealth(h);
      setLibraryStatus(lib);
      setSpotifyAuth(spotStatus);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("spotify") === "connected") {
      setSuccess("Spotify connected successfully!");
      setSpotifyAuth({ authenticated: true });
      window.history.replaceState({}, "", window.location.pathname);
    } else if (params.get("spotify") === "error") {
      setError(`Spotify connection failed: ${params.get("detail") || "Unknown error"}`);
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      await updateConfig(config);
      setSuccess("Settings saved successfully");
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(`Save failed: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleSpotifyConnect = async () => {
    setSpotifyConnecting(true);
    setError(null);
    try {
      const { url } = await getSpotifyAuthUrl();
      window.location.href = url;
    } catch (err) {
      setError(`Spotify auth failed: ${err.message}`);
      setSpotifyConnecting(false);
    }
  };

  const handleLibraryScan = async () => {
    setLibraryScanning(true);
    try {
      const result = await scanLibrary();
      setLibraryStatus((prev) => ({
        ...prev,
        track_count: result.track_count,
        is_scanning: false,
      }));
    } catch (err) {
      setError(`Library scan failed: ${err.message}`);
    } finally {
      setLibraryScanning(false);
    }
  };

  const update = (path, value) => {
    setConfig((prev) => {
      const next = structuredClone(prev);
      const keys = path.split(".");
      let obj = next;
      for (let i = 0; i < keys.length - 1; i++) {
        obj = obj[keys[i]];
      }
      obj[keys[keys.length - 1]] = value;
      return next;
    });
  };

  const updateMapping = (field, genre, value) => {
    setConfig((prev) => {
      const next = structuredClone(prev);
      next[field][genre] = value;
      return next;
    });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-400 animate-pulse">Loading settings...</div>
      </div>
    );
  }

  if (!config) {
    return (
      <div className="bg-red-500/10 border border-red-500/30 text-red-400 text-sm rounded px-4 py-3">
        Failed to load configuration. {error}
      </div>
    );
  }

  return (
    <div className="max-w-3xl space-y-6">
      {/* Feedback banners */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-400 text-sm rounded px-4 py-2">
          {error}
        </div>
      )}
      {success && (
        <div className="bg-green-500/10 border border-green-500/30 text-green-400 text-sm rounded px-4 py-2">
          {success}
        </div>
      )}

      {/* System Status */}
      <SystemStatus
        health={health}
        libraryStatus={libraryStatus}
        libraryScanning={libraryScanning}
        onLibraryScan={handleLibraryScan}
      />

      {/* Spotify Connection */}
      <SpotifyConnection
        spotifyAuth={spotifyAuth}
        connecting={spotifyConnecting}
        onConnect={handleSpotifyConnect}
        hasCredentials={!!(config.spotify?.client_id && config.spotify?.client_secret)}
      />

      {/* Paths */}
      <section className={SECTION_CLASS}>
        <h3 className="text-sm font-semibold text-gray-200">Paths</h3>
        <div className="grid grid-cols-2 gap-4">
          <Field
            label="Downloads Folder"
            value={config.downloads_folder}
            onChange={(v) => update("downloads_folder", v)}
            placeholder="~/Downloads"
          />
          <Field
            label="External Drive"
            value={config.external_drive_path}
            onChange={(v) => update("external_drive_path", v)}
            placeholder="/Volumes/My Passport/Music/iTunes/iTunes Media/Music/Unknown Artist/Unknown Album"
          />
        </div>
      </section>

      {/* Spotify */}
      <section className={SECTION_CLASS}>
        <h3 className="text-sm font-semibold text-gray-200">Spotify</h3>
        <div className="grid grid-cols-2 gap-4">
          <Field
            label="Client ID"
            value={config.spotify?.client_id}
            onChange={(v) => update("spotify.client_id", v)}
            placeholder="Your Spotify Client ID"
          />
          <Field
            label="Client Secret"
            value={config.spotify?.client_secret}
            onChange={(v) => update("spotify.client_secret", v)}
            placeholder="Your Spotify Client Secret"
            type="password"
          />
        </div>
        <Field
          label="Redirect URI"
          value={config.spotify?.redirect_uri}
          onChange={(v) => update("spotify.redirect_uri", v)}
          placeholder="http://127.0.0.1:8888/callback"
        />
      </section>

      {/* Supabase */}
      <section className={SECTION_CLASS}>
        <h3 className="text-sm font-semibold text-gray-200">Supabase</h3>
        <Field
          label="Project URL"
          value={config.supabase?.url}
          onChange={(v) => update("supabase.url", v)}
          placeholder="https://your-project.supabase.co"
        />
        <Field
          label="Anon Key"
          value={config.supabase?.anon_key}
          onChange={(v) => update("supabase.anon_key", v)}
          placeholder="Your Supabase anon key"
          type="password"
        />
      </section>

      {/* Playlist Mapping */}
      <section className={SECTION_CLASS}>
        <h3 className="text-sm font-semibold text-gray-200">
          Genre → iTunes Playlist Mapping
        </h3>
        <p className="text-xs text-gray-500">
          Apple Music playlist name for each genre. Must match exactly
          (case-sensitive).
        </p>
        <div className="space-y-2">
          {GENRES.map((genre) => (
            <div key={genre} className="flex items-center gap-3">
              <span className="text-xs text-gray-300 w-32 flex-shrink-0">
                {genre}
              </span>
              <input
                className={INPUT_CLASS}
                value={config.playlist_mapping?.[genre] || ""}
                onChange={(e) =>
                  updateMapping("playlist_mapping", genre, e.target.value)
                }
                placeholder="iTunes playlist name"
              />
            </div>
          ))}
        </div>
      </section>

      {/* Pipeline Settings */}
      <section className={SECTION_CLASS}>
        <h3 className="text-sm font-semibold text-gray-200">Pipeline</h3>
        <div className="grid grid-cols-2 gap-4">
          <Field
            label="Poll Interval (minutes)"
            value={config.poll_interval_minutes}
            onChange={(v) => update("poll_interval_minutes", parseInt(v, 10) || 30)}
            type="number"
            placeholder="30"
          />
          <div>
            <label className={LABEL_CLASS}>File Watch</label>
            <button
              onClick={() =>
                update("file_watch_enabled", !config.file_watch_enabled)
              }
              className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
                config.file_watch_enabled
                  ? "bg-green-500/20 text-green-400 border border-green-500/30"
                  : "bg-gray-500/20 text-gray-400 border border-gray-500/30"
              }`}
            >
              {config.file_watch_enabled ? "Enabled" : "Disabled"}
            </button>
          </div>
        </div>
      </section>

      {/* Save */}
      <div className="flex items-center gap-3 pt-2">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-5 py-2 rounded bg-accent text-white text-sm font-medium
                     hover:bg-accent-hover disabled:opacity-50 transition-colors"
        >
          {saving ? "Saving..." : "Save Settings"}
        </button>
        <button
          onClick={fetchConfig}
          className="px-4 py-2 rounded text-sm text-gray-400 border border-base-600
                     hover:text-gray-200 hover:border-gray-500 transition-colors"
        >
          Reset
        </button>
      </div>
    </div>
  );
}

function Field({ label, value, onChange, placeholder, type = "text" }) {
  return (
    <div>
      <label className={LABEL_CLASS}>{label}</label>
      <input
        type={type}
        className={INPUT_CLASS}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
    </div>
  );
}

function SystemStatus({ health, libraryStatus, libraryScanning, onLibraryScan }) {
  return (
    <section className={SECTION_CLASS}>
      <h3 className="text-sm font-semibold text-gray-200">System Status</h3>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatusCard
          label="External Drive"
          ok={health?.drive_mounted}
          detail={health?.drive_mounted ? health.drive_path : "Not mounted"}
        />
        <StatusCard
          label="Supabase"
          ok={health?.supabase}
          detail={health?.supabase ? "Connected" : "Disconnected"}
        />
        <StatusCard
          label="Apple Music"
          ok={health?.music_app}
          detail={health?.music_app ? "Running" : "Not running"}
        />
        <StatusCard
          label="iTunes Cache"
          ok={libraryStatus?.track_count > 0}
          detail={
            libraryStatus?.track_count
              ? `${libraryStatus.track_count} tracks`
              : "Not loaded"
          }
        />
      </div>
      <div className="pt-1">
        <button
          onClick={onLibraryScan}
          disabled={libraryScanning}
          className="text-xs px-3 py-1.5 rounded border transition-colors
                     bg-accent/20 text-accent hover:bg-accent/30 border-accent/30
                     disabled:opacity-50"
        >
          {libraryScanning ? "Scanning..." : "Refresh iTunes Library"}
        </button>
      </div>
    </section>
  );
}

function SpotifyConnection({ spotifyAuth, connecting, onConnect, hasCredentials }) {
  const isConnected = spotifyAuth?.authenticated;
  const needsScopeUpgrade = isConnected && spotifyAuth?.has_streaming_scope === false;

  return (
    <section className={SECTION_CLASS}>
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-200">Spotify Connection</h3>
        <span
          className={`text-xs px-2 py-0.5 rounded-full ${
            isConnected
              ? "bg-green-500/15 text-green-400 border border-green-500/30"
              : "bg-yellow-500/15 text-yellow-400 border border-yellow-500/30"
          }`}
        >
          {isConnected ? "Connected" : "Not connected"}
        </span>
      </div>

      {needsScopeUpgrade && (
        <div className="bg-amber-500/10 border border-amber-500/20 rounded px-4 py-3 text-sm space-y-2">
          <p className="text-amber-300 font-medium">Playback permissions needed</p>
          <p className="text-amber-400/80 text-xs">
            Your Spotify connection needs updated permissions for in-app playback.
            Click <strong>Reconnect Spotify</strong> below to re-authorize with the new scopes.
          </p>
        </div>
      )}

      {!hasCredentials && (
        <div className="bg-yellow-500/10 border border-yellow-500/20 rounded px-4 py-3 text-sm space-y-2">
          <p className="text-yellow-300 font-medium">Spotify credentials required</p>
          <ol className="text-yellow-400/80 text-xs space-y-1 list-decimal list-inside">
            <li>
              Go to{" "}
              <a
                href="https://developer.spotify.com/dashboard"
                target="_blank"
                rel="noopener noreferrer"
                className="text-accent underline"
              >
                developer.spotify.com/dashboard
              </a>
            </li>
            <li>Create an app (or use an existing one)</li>
            <li>
              Add <code className="bg-base-700 px-1 rounded text-xs">http://127.0.0.1:8888/callback</code>{" "}
              as a Redirect URI in the app settings
            </li>
            <li>Copy the Client ID and Client Secret into the fields below</li>
            <li>Click <strong>Save Settings</strong>, then come back and click <strong>Connect to Spotify</strong></li>
          </ol>
        </div>
      )}

      {hasCredentials && !isConnected && (
        <div className="flex items-center gap-3">
          <button
            onClick={onConnect}
            disabled={connecting}
            className="px-4 py-2 rounded bg-green-600 text-white text-sm font-medium
                       hover:bg-green-500 disabled:opacity-50 transition-colors"
          >
            {connecting ? "Redirecting..." : "Connect to Spotify"}
          </button>
          <span className="text-xs text-gray-500">
            Opens Spotify to authorize this app
          </span>
        </div>
      )}

      {isConnected && (
        <div className="space-y-2">
          <p className="text-xs text-gray-500">
            Your Spotify account is linked. You can scan playlists from the header.
          </p>
          <div className="flex items-center gap-3">
            <button
              onClick={onConnect}
              disabled={connecting}
              className="px-3 py-1.5 rounded border border-base-500 text-gray-400 text-xs
                         hover:text-gray-200 hover:border-gray-400 disabled:opacity-50 transition-colors"
            >
              {connecting ? "Redirecting..." : "Reconnect Spotify"}
            </button>
            <span className="text-xs text-gray-600">
              Use this if playback isn't working (re-grants permissions)
            </span>
          </div>
        </div>
      )}
    </section>
  );
}

function StatusCard({ label, ok, detail }) {
  return (
    <div className="bg-base-900 rounded-lg px-3 py-2.5 border border-base-600">
      <div className="flex items-center gap-1.5 mb-1">
        <span
          className={`inline-block w-2 h-2 rounded-full ${
            ok === true
              ? "bg-green-400"
              : ok === false
                ? "bg-red-400"
                : "bg-gray-600"
          }`}
        />
        <span className="text-xs font-medium text-gray-300">{label}</span>
      </div>
      <span className="text-xs text-gray-500">{detail}</span>
    </div>
  );
}
