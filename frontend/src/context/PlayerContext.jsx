import { createContext, useContext, useState, useEffect, useRef, useCallback } from "react";
import { getSpotifyToken } from "../api";

const PlayerContext = createContext(null);

const PLAYER_NAME = "DJ Track Pipeline";
const TOKEN_REFRESH_BUFFER_MS = 60_000;

export function PlayerProvider({ children }) {
  const [isReady, setIsReady] = useState(false);
  const [deviceId, setDeviceId] = useState(null);
  const [currentTrack, setCurrentTrack] = useState(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolumeState] = useState(0.5);
  const [error, setError] = useState(null);

  const playerRef = useRef(null);
  const tokenRef = useRef(null);
  const positionTimerRef = useRef(null);

  const fetchToken = useCallback(async () => {
    try {
      const { access_token } = await getSpotifyToken();
      tokenRef.current = access_token;
      return access_token;
    } catch (err) {
      setError("Could not fetch Spotify token — are you logged in?");
      return null;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    const initPlayer = async () => {
      const token = await fetchToken();
      if (!token || cancelled) return;

      // Wait for the SDK script to define window.Spotify
      if (!window.Spotify) {
        await new Promise((resolve) => {
          window.onSpotifyWebPlaybackSDKReady = resolve;
        });
      }
      if (cancelled) return;

      const player = new window.Spotify.Player({
        name: PLAYER_NAME,
        getOAuthToken: async (cb) => {
          const t = await fetchToken();
          if (t) cb(t);
        },
        volume: volume,
      });

      player.addListener("ready", ({ device_id }) => {
        setDeviceId(device_id);
        setIsReady(true);
        setError(null);
      });

      player.addListener("not_ready", () => {
        setIsReady(false);
        setDeviceId(null);
      });

      player.addListener("player_state_changed", (state) => {
        if (!state) {
          setIsPlaying(false);
          setCurrentTrack(null);
          return;
        }

        const track = state.track_window?.current_track;
        if (track) {
          setCurrentTrack({
            spotifyId: track.id,
            name: track.name,
            artist: track.artists.map((a) => a.name).join(", "),
            albumArt: track.album?.images?.[0]?.url || null,
          });
        }

        setIsPlaying(!state.paused);
        setPosition(state.position);
        setDuration(state.duration);
      });

      player.addListener("initialization_error", ({ message }) => {
        setError(`Player init error: ${message}`);
      });

      player.addListener("authentication_error", ({ message }) => {
        setError(`Auth error: ${message}. Try reconnecting Spotify.`);
        setIsReady(false);
      });

      player.addListener("account_error", ({ message }) => {
        setError(`Account error: ${message}. Spotify Premium is required.`);
      });

      const success = await player.connect();
      if (!success) {
        setError("Failed to connect to Spotify. Is Premium active?");
      }

      playerRef.current = player;
    };

    initPlayer();

    return () => {
      cancelled = true;
      if (playerRef.current) {
        playerRef.current.disconnect();
        playerRef.current = null;
      }
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Tick position forward while playing
  useEffect(() => {
    if (positionTimerRef.current) clearInterval(positionTimerRef.current);

    if (isPlaying) {
      positionTimerRef.current = setInterval(() => {
        setPosition((prev) => Math.min(prev + 500, duration));
      }, 500);
    }

    return () => {
      if (positionTimerRef.current) clearInterval(positionTimerRef.current);
    };
  }, [isPlaying, duration]);

  const play = useCallback(
    async (spotifyId, trackName, artistName) => {
      if (!deviceId || !tokenRef.current) return;

      try {
        const res = await fetch(
          `https://api.spotify.com/v1/me/player/play?device_id=${deviceId}`,
          {
            method: "PUT",
            headers: {
              Authorization: `Bearer ${tokenRef.current}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ uris: [`spotify:track:${spotifyId}`] }),
          }
        );

        if (res.status === 401) {
          const newToken = await fetchToken();
          if (!newToken) return;
          await fetch(
            `https://api.spotify.com/v1/me/player/play?device_id=${deviceId}`,
            {
              method: "PUT",
              headers: {
                Authorization: `Bearer ${newToken}`,
                "Content-Type": "application/json",
              },
              body: JSON.stringify({ uris: [`spotify:track:${spotifyId}`] }),
            }
          );
        }

        if (!res.ok && res.status !== 401) {
          const text = await res.text().catch(() => "");
          console.error("Spotify play failed:", res.status, text);
        }
      } catch (err) {
        console.error("Play request failed:", err);
      }
    },
    [deviceId, fetchToken]
  );

  const togglePlayback = useCallback(async () => {
    if (!playerRef.current) return;
    await playerRef.current.togglePlay();
  }, []);

  const seek = useCallback(async (ms) => {
    if (!playerRef.current) return;
    await playerRef.current.seek(ms);
    setPosition(ms);
  }, []);

  const setVolume = useCallback(async (val) => {
    const clamped = Math.max(0, Math.min(1, val));
    setVolumeState(clamped);
    if (playerRef.current) {
      await playerRef.current.setVolume(clamped);
    }
  }, []);

  const value = {
    isReady,
    deviceId,
    currentTrack,
    isPlaying,
    position,
    duration,
    volume,
    error,
    play,
    togglePlayback,
    seek,
    setVolume,
  };

  return (
    <PlayerContext.Provider value={value}>{children}</PlayerContext.Provider>
  );
}

export function usePlayer() {
  const ctx = useContext(PlayerContext);
  if (!ctx) {
    throw new Error("usePlayer must be used within a PlayerProvider");
  }
  return ctx;
}
