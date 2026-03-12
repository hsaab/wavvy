import { useRef, useCallback } from "react";
import { usePlayer } from "../context/PlayerContext";

function formatTime(ms) {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

function PauseIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
      <rect x="5" y="3" width="3.5" height="14" rx="1" />
      <rect x="11.5" y="3" width="3.5" height="14" rx="1" />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
      <path d="M6 3.5L16 10L6 16.5V3.5Z" />
    </svg>
  );
}

function VolumeIcon({ level }) {
  if (level === 0) {
    return (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
        <path d="M8 2.8L4.5 6H2v4h2.5L8 13.2V2.8Z" />
        <path d="M11 5.5l3 3m0-3l-3 3" stroke="currentColor" strokeWidth="1.3" fill="none" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
      <path d="M8 2.8L4.5 6H2v4h2.5L8 13.2V2.8Z" />
      {level > 0.3 && <path d="M10.5 5.5a4 4 0 010 5" stroke="currentColor" strokeWidth="1.3" fill="none" strokeLinecap="round" />}
      {level > 0.65 && <path d="M12 3.5a6.5 6.5 0 010 9" stroke="currentColor" strokeWidth="1.3" fill="none" strokeLinecap="round" />}
    </svg>
  );
}

export default function PlayerBar() {
  const {
    isReady,
    currentTrack,
    isPlaying,
    position,
    duration,
    volume,
    error,
    togglePlayback,
    seek,
    setVolume,
  } = usePlayer();

  const progressBarRef = useRef(null);
  const volumeBarRef = useRef(null);

  const handleProgressClick = useCallback(
    (e) => {
      if (!progressBarRef.current || !duration) return;
      const rect = progressBarRef.current.getBoundingClientRect();
      const fraction = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      seek(Math.floor(fraction * duration));
    },
    [duration, seek]
  );

  const handleVolumeClick = useCallback(
    (e) => {
      if (!volumeBarRef.current) return;
      const rect = volumeBarRef.current.getBoundingClientRect();
      const fraction = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      setVolume(fraction);
    },
    [setVolume]
  );

  if (!isReady && !error) return null;

  if (error) {
    return (
      <div className="fixed bottom-0 left-0 right-0 h-12 bg-base-800 border-t border-red-500/30
                      flex items-center justify-center text-xs text-red-400 px-4 z-50">
        {error}
      </div>
    );
  }

  const progressPercent = duration > 0 ? (position / duration) * 100 : 0;

  return (
    <div className="fixed bottom-0 left-0 right-0 h-16 bg-base-800 border-t border-base-600
                    flex items-center px-4 gap-4 z-50 select-none">
      {/* Left: Track info */}
      <div className="flex items-center gap-3 min-w-0 w-56 shrink-0">
        {currentTrack?.albumArt && (
          <img
            src={currentTrack.albumArt}
            alt=""
            className="w-10 h-10 rounded object-cover"
          />
        )}
        {currentTrack ? (
          <div className="min-w-0">
            <div className="text-sm text-gray-100 font-medium truncate">
              {currentTrack.name}
            </div>
            <div className="text-xs text-gray-400 truncate">
              {currentTrack.artist}
            </div>
          </div>
        ) : (
          <div className="text-xs text-gray-500">No track selected</div>
        )}
      </div>

      {/* Center: Controls + progress */}
      <div className="flex-1 flex flex-col items-center gap-1 max-w-xl mx-auto">
        <button
          onClick={togglePlayback}
          disabled={!currentTrack}
          className="w-8 h-8 flex items-center justify-center rounded-full
                     bg-gray-100 text-base-900 hover:scale-105 transition-transform
                     disabled:opacity-30 disabled:hover:scale-100"
        >
          {isPlaying ? <PauseIcon /> : <PlayIcon />}
        </button>

        <div className="w-full flex items-center gap-2 text-[10px] text-gray-400">
          <span className="w-8 text-right tabular-nums">{formatTime(position)}</span>
          <div
            ref={progressBarRef}
            onClick={handleProgressClick}
            className="flex-1 h-1 bg-base-600 rounded-full cursor-pointer group relative"
          >
            <div
              className="h-full bg-accent rounded-full transition-[width] duration-200"
              style={{ width: `${progressPercent}%` }}
            />
            <div
              className="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-gray-100 rounded-full
                         opacity-0 group-hover:opacity-100 transition-opacity shadow"
              style={{ left: `calc(${progressPercent}% - 6px)` }}
            />
          </div>
          <span className="w-8 tabular-nums">{formatTime(duration)}</span>
        </div>
      </div>

      {/* Right: Volume */}
      <div className="flex items-center gap-2 w-36 shrink-0 justify-end">
        <button
          onClick={() => setVolume(volume > 0 ? 0 : 0.5)}
          className="text-gray-400 hover:text-gray-200 transition-colors"
        >
          <VolumeIcon level={volume} />
        </button>
        <div
          ref={volumeBarRef}
          onClick={handleVolumeClick}
          className="w-20 h-1 bg-base-600 rounded-full cursor-pointer group relative"
        >
          <div
            className="h-full bg-gray-400 rounded-full"
            style={{ width: `${volume * 100}%` }}
          />
          <div
            className="absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 bg-gray-100 rounded-full
                       opacity-0 group-hover:opacity-100 transition-opacity shadow"
            style={{ left: `calc(${volume * 100}% - 5px)` }}
          />
        </div>
      </div>
    </div>
  );
}
