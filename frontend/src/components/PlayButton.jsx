import { useCallback } from "react";
import { usePlayer } from "../context/PlayerContext";

export default function PlayButton({ spotifyId, trackName, artistName }) {
  const { isReady, currentTrack, isPlaying, play, togglePlayback } = usePlayer();

  const isThisTrack = currentTrack?.spotifyId === spotifyId;
  const isThisPlaying = isThisTrack && isPlaying;

  const handleClick = useCallback(
    (e) => {
      e.stopPropagation();
      if (isThisTrack) {
        togglePlayback();
      } else {
        play(spotifyId, trackName, artistName);
      }
    },
    [isThisTrack, spotifyId, trackName, artistName, play, togglePlayback]
  );

  if (!isReady) return <div className="w-7 h-7" />;

  return (
    <button
      onClick={handleClick}
      className={`w-7 h-7 flex items-center justify-center rounded-full transition-all
        ${isThisPlaying
          ? "bg-accent text-white scale-105"
          : "bg-base-600 text-gray-300 hover:bg-accent/70 hover:text-white hover:scale-105"
        }`}
      title={isThisPlaying ? "Pause" : `Play ${trackName}`}
    >
      {isThisPlaying ? (
        <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
          <rect x="2" y="1.5" width="2.8" height="9" rx="0.7" />
          <rect x="7.2" y="1.5" width="2.8" height="9" rx="0.7" />
        </svg>
      ) : (
        <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
          <path d="M3 1.5L10.5 6L3 10.5V1.5Z" />
        </svg>
      )}
    </button>
  );
}
