/**
 * Centralized API helper for backend communication.
 * All fetch calls go through here for consistent error handling.
 */

const BASE = "";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error");
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

// Health
export const getHealth = () => request("/api/health");

// Tracks
export const getTracks = (status) =>
  request(status ? `/api/tracks?status=${status}` : "/api/tracks");

export const searchTracks = (query) =>
  request(`/api/tracks?search=${encodeURIComponent(query)}`);

export const getTrackCounts = () => request("/api/tracks/counts");

export const updateTrack = (trackId, fields) =>
  request(`/api/tracks/${trackId}`, {
    method: "PATCH",
    body: JSON.stringify(fields),
  });

// Playlists
export const getPlaylists = () => request("/api/playlists");

// Scan
export const triggerScan = (playlistIds) =>
  request("/api/scan", {
    method: "POST",
    body: JSON.stringify({ playlist_ids: playlistIds }),
  });

// Link resolution
export const resolveLinks = (trackIds) =>
  request("/api/resolve", {
    method: "POST",
    body: JSON.stringify({ track_ids: trackIds }),
  });

// Cart
export const buildCart = (store) =>
  request("/api/cart/build", {
    method: "POST",
    body: JSON.stringify({ store }),
  });

export const getCartStatus = () => request("/api/cart/status");

// Config
export const getConfig = () => request("/api/config");

export const updateConfig = (config) =>
  request("/api/config", {
    method: "PUT",
    body: JSON.stringify(config),
  });

// Credentials status (env-based secrets)
export const getCredentialsStatus = () => request("/api/credentials/status");

// Spotify auth & playback
export const getSpotifyStatus = () => request("/api/spotify/status");

export const getSpotifyAuthUrl = () => request("/api/spotify/auth-url");

export const getSpotifyToken = () => request("/api/spotify/token");

// iTunes library
export const scanLibrary = () =>
  request("/api/library/scan", { method: "POST" });

export const getLibraryStatus = () => request("/api/library/status");

export const getLibraryPlaylists = () =>
  request("/api/library/playlists").then((r) => r.playlists);

export const addTrackToPlaylists = (trackId) =>
  request(`/api/tracks/${trackId}/add-to-playlists`, { method: "POST" });

// File pipeline
export const getPipelineStatus = () => request("/api/pipeline/status");

export const assignFile = (filepath, trackId) =>
  request("/api/pipeline/assign", {
    method: "POST",
    body: JSON.stringify({ filepath, track_id: trackId }),
  });
