"""Filename matching for Downloads → buy-queue rows.

Watchdog and Scan Downloads share this module. Open statuses can be claimed.
A downloaded row is only rematched when its download_path is missing or gone.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Literal

from thefuzz import fuzz

AUDIO_EXTENSIONS = {".wav", ".mp3"}
# downloaded is not in this set — claimed rows stay sticky unless orphaned.
OPEN_MATCH_STATUSES = ("carted", "purchased", "approved", "cart_failed")
MATCH_THRESHOLD = 80

ClaimKind = Literal["existing", "open", "orphan", "none"]

_MIX_SUFFIX_RE = re.compile(
    r"\s*-\s*(Original Mix|Extended Mix|Radio Edit|Edit|Remix)\s*$",
    re.IGNORECASE,
)


def _strip_diacritics(text: str) -> str:
    """Remove accent marks / diacritics (e.g. ï → i, é → e)."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_text(text: str) -> str:
    """Shared normalization for both filenames and DB strings."""
    text = _strip_diacritics(text)
    text = text.replace("_", " ")
    text = text.replace("'", "").replace("\u2019", "").replace("`", "")
    text = re.sub(r"\s*\(.*?\)\s*", " ", text)
    text = re.sub(r"\s*\[.*?\]\s*", " ", text)
    text = _MIX_SUFFIX_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def normalize_filename(name: str) -> str:
    """Strip common Beatport/Traxsource filename noise for matching."""
    return normalize_text(Path(name).stem)


def split_artist_title(filename: str) -> tuple[str, str]:
    """Split a Beatport-style filename into (artist, title)."""
    stem = Path(filename).stem
    if " - " in stem:
        parts = stem.split(" - ", 1)
        return parts[0].strip(), parts[1].strip()
    return "", stem.strip()


def score_against_track(filename: str, track: dict[str, Any]) -> int:
    """Return a 0-100 fuzzy score for how well *filename* matches *track*."""
    norm = normalize_filename(filename)
    artist_part, title_part = split_artist_title(filename)

    db_artist = normalize_text(track.get("artist_name") or "")
    db_title = normalize_text(track.get("track_name") or "")
    db_combined = f"{db_artist} {db_title}".strip()

    scores: list[int] = [
        fuzz.token_sort_ratio(norm, db_combined),
        fuzz.token_set_ratio(norm, db_combined),
    ]

    if artist_part:
        artist_norm = normalize_text(artist_part)
        title_norm = normalize_text(title_part)
        artist_sort = fuzz.token_sort_ratio(artist_norm, db_artist)
        title_sort = fuzz.token_sort_ratio(title_norm, db_title)
        scores.append(int(artist_sort * 0.35 + title_sort * 0.65))
        artist_set = fuzz.token_set_ratio(artist_norm, db_artist)
        title_set = fuzz.token_set_ratio(title_norm, db_title)
        scores.append(int(artist_set * 0.35 + title_set * 0.65))

    return max(scores)


def list_audio_files(folder: Path) -> list[Path]:
    """Return audio files in *folder* (non-recursive, skip dotfiles)."""
    return sorted(
        (
            path
            for path in folder.iterdir()
            if path.is_file()
            and not path.name.startswith(".")
            and path.suffix.lower() in AUDIO_EXTENSIONS
        ),
        key=lambda path: path.name.lower(),
    )


def best_match(
    filename: str,
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, int]:
    """Return the best candidate at or above MATCH_THRESHOLD, else (None, score)."""
    if not candidates:
        return None, 0

    best_track: dict[str, Any] | None = None
    best_score = 0
    for track in candidates:
        score = score_against_track(filename, track)
        if score > best_score:
            best_score = score
            best_track = track

    if best_score >= MATCH_THRESHOLD:
        return best_track, best_score
    return None, best_score


def _resolved_download_path(track: dict[str, Any]) -> Path | None:
    raw = track.get("download_path") or ""
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except OSError:
        return None


def existing_assignment(
    path: Path,
    downloaded: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the downloaded row whose download_path is this file."""
    try:
        resolved = path.resolve()
    except OSError:
        return None
    for track in downloaded:
        assigned = _resolved_download_path(track)
        if assigned is not None and assigned == resolved:
            return track
    return None


def orphaned_downloaded(downloaded: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Downloaded rows whose file is missing — safe to rematch."""
    orphans: list[dict[str, Any]] = []
    for track in downloaded:
        assigned = _resolved_download_path(track)
        if assigned is None or not assigned.exists():
            orphans.append(track)
    return orphans


def consume_track(candidates: list[dict[str, Any]], track_id: int) -> None:
    """Remove *track_id* from a working candidate list."""
    candidates[:] = [row for row in candidates if row["id"] != track_id]


def pick_track_for_file(
    path: Path,
    open_candidates: list[dict[str, Any]],
    downloaded: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, int, ClaimKind]:
    """Choose a row for *path* without mutating the lists.

    Preference: existing download_path → open queue → orphaned downloaded.
    """
    existing = existing_assignment(path, downloaded)
    if existing is not None:
        return existing, 100, "existing"

    track, score = best_match(path.name, open_candidates)
    if track is not None:
        return track, score, "open"

    track, score = best_match(path.name, orphaned_downloaded(downloaded))
    if track is not None:
        return track, score, "orphan"
    return None, score, "none"
