"""Store-track identity: parse mix/artists and score Beatport/Traxsource hits.

Mix identity is a hard gate for named remixes. Title, artists, and mix are
scored as separate fields. URL slugs are title and mix evidence only, never
performer credits. Do not reuse file_pipeline._normalize_text (it strips mix).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from thefuzz import fuzz

logger = logging.getLogger(__name__)

REMIXER_OVERLAP = 80
TITLE_FLOOR = 60
MIX_PENALTY = 20
CONFIRMED_TITLE_WEIGHT = 0.7
CONFIRMED_ARTIST_WEIGHT = 0.3
UNCONFIRMED_TITLE_WEIGHT = 0.6
UNCONFIRMED_ARTIST_WEIGHT = 0.4

_MIX_MARKER_RE = re.compile(r"\b(remix|mix|edit|dub|version)\b", re.IGNORECASE)
_REMIX_WORD_RE = re.compile(r"\bremix\b", re.IGNORECASE)
_ORIGINAL_WORD_RE = re.compile(r"\boriginal\b", re.IGNORECASE)
_GROUP_RE = re.compile(r"[\(\[]([^\)\]]+)[\)\]]")
_TRAILING_HYPHEN_RE = re.compile(r"^(.*)\s+[-–—]\s+(.+)$")
_ARTIST_SPLIT_RE = re.compile(
    r"\s*,\s*|\s+&\s+|\s+feat\.?\s+|\s+vs\.?\s+|\s+x\s+",
    re.IGNORECASE,
)
_MIX_TAIL_RE = re.compile(
    r"\s*((?:original|extended|radio)\s+)?(remix|mix|edit|dub|version)\s*$",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


@dataclass
class StoreQuery:
    """Parsed Spotify (or other source) identity used to search and score."""

    title_core: str
    artists: list[str]
    mix_kind: str
    mix_label: str
    remixers: list[str]


@dataclass
class StoreCandidate:
    """Raw store search row. Traxsource leaves mix_name and isrc as None."""

    title: str
    artist: str
    url: str
    slug: str
    track_id: str | int | None
    mix_name: str | None
    isrc: str | None


@dataclass
class StoreHit:
    """Parsed store row. slug_text is title/mix evidence, not artist credits."""

    title_core: str
    artists: list[str]
    mix_kind: str
    mix_label: str
    remixers: list[str]
    slug_text: str
    url: str
    mix_name: str | None
    isrc: str | None


def parse_store_query(artist: str, title: str) -> StoreQuery:
    """Parse source artist/title into core title, artists, and mix identity."""
    title_core, artists, mix_kind, mix_label, remixers = _parse_identity(
        artist or "", title or "",
    )
    return StoreQuery(
        title_core=title_core,
        artists=artists,
        mix_kind=mix_kind,
        mix_label=mix_label,
        remixers=remixers,
    )


def parse_store_hit(
    title: str,
    artist: str,
    url: str,
    mix_name: str | None = None,
    isrc: str | None = None,
) -> StoreHit:
    """Parse a store candidate. Mix may also be recovered from the URL slug."""
    title_core, artists, mix_kind, mix_label, remixers = _parse_identity(
        artist or "", title or "",
    )
    slug_text = _slug_text_from_url(url or "")
    if mix_kind == "unknown":
        slug_mix = _mix_label_from_slug(title_core, slug_text)
        if slug_mix:
            mix_label = slug_mix
            mix_kind = _classify_mix(mix_label)
            remixers = _remixers_from_label(mix_label, mix_kind)
    return StoreHit(
        title_core=title_core,
        artists=artists,
        mix_kind=mix_kind,
        mix_label=mix_label,
        remixers=remixers,
        slug_text=slug_text,
        url=url or "",
        mix_name=mix_name,
        isrc=isrc,
    )


def build_search_query(query: StoreQuery) -> str:
    """One search string: title plus first remixer, or title plus first artist."""
    if query.mix_kind == "remix" and query.remixers:
        return f"{query.title_core} {query.remixers[0]}"
    if query.artists:
        return f"{query.title_core} {query.artists[0]}"
    return query.title_core


def score_hit(
    query: StoreQuery,
    hit: StoreHit,
    isrc: str | None = None,
) -> int:
    """Score a store hit. Named remixes are a hard gate; originals are a soft preference.

    A matching Spotify ISRC (trim, case-insensitive) accepts at 100 immediately.
    ISRC is a result key, not a search query.
    """
    if _isrcs_match(isrc, hit.isrc):
        return 100

    mix_confirmed = False
    if query.mix_kind == "remix":
        if _is_original_hit(hit) or _is_no_mix_hit(hit):
            logger.debug(
                "Remix gate rejected %s: original or no-mix hit", hit.url,
            )
            return 0
        if not _remixer_overlap(query, hit):
            logger.debug(
                "Remix gate rejected %s: no remixer overlap", hit.url,
            )
            return 0
        mix_confirmed = True

    title_score = fuzz.token_sort_ratio(query.title_core, hit.title_core)
    # Slug mix words must not carry a hit whose base title does not match.
    if title_score < TITLE_FLOOR:
        return int(title_score)

    artist_score = _artist_score(query, hit)
    if mix_confirmed:
        if artist_score is None:
            combined = title_score * CONFIRMED_TITLE_WEIGHT
        else:
            combined = (
                title_score * CONFIRMED_TITLE_WEIGHT
                + artist_score * CONFIRMED_ARTIST_WEIGHT
            )
    else:
        if artist_score is None:
            combined = float(title_score)
        else:
            combined = (
                title_score * UNCONFIRMED_TITLE_WEIGHT
                + artist_score * UNCONFIRMED_ARTIST_WEIGHT
            )
        if query.mix_kind in ("original", "unknown") and _is_remix_hit(hit):
            combined -= MIX_PENALTY

    return max(0, int(combined))


def _parse_identity(
    artist: str, title: str,
) -> tuple[str, list[str], str, str, list[str]]:
    title_core, mix_label = _split_mix(title)
    mix_kind = _classify_mix(mix_label)
    remixers = _remixers_from_label(mix_label, mix_kind)
    return title_core, _split_artists(artist), mix_kind, mix_label, remixers


def _split_mix(title: str) -> tuple[str, str]:
    """Pull a mix label from parens, brackets, or a trailing hyphen suffix."""
    title = re.sub(r"\s+", " ", title).strip()
    if not title:
        return "", ""

    for match in reversed(list(_GROUP_RE.finditer(title))):
        inner = match.group(1).strip()
        if _MIX_MARKER_RE.search(inner):
            core = f"{title[:match.start()]} {title[match.end():]}"
            return re.sub(r"\s+", " ", core).strip(), inner

    hyphen = _TRAILING_HYPHEN_RE.match(title)
    if hyphen and _MIX_MARKER_RE.search(hyphen.group(2)):
        return hyphen.group(1).strip(), hyphen.group(2).strip()

    return title, ""


def _classify_mix(mix_label: str) -> str:
    if not mix_label:
        return "unknown"
    if _REMIX_WORD_RE.search(mix_label):
        return "remix"
    if _ORIGINAL_WORD_RE.search(mix_label):
        return "original"
    return "unknown"


def _remixers_from_label(mix_label: str, mix_kind: str) -> list[str]:
    if mix_kind != "remix" or not mix_label:
        return []
    name = _MIX_TAIL_RE.sub("", mix_label).strip()
    return _split_artists(name) if name else []


def _split_artists(raw: str) -> list[str]:
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return []
    return [part.strip() for part in _ARTIST_SPLIT_RE.split(raw) if part.strip()]


def _slug_text_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return ""
    if parts[-1].isdigit() and len(parts) >= 2:
        return parts[-2]
    return parts[-1]


def _mix_label_from_slug(title_core: str, slug_text: str) -> str:
    slug_tokens = [t for t in slug_text.replace("-", " ").lower().split() if t]
    if not slug_tokens or not _MIX_MARKER_RE.search(" ".join(slug_tokens)):
        return ""
    core_tokens = _PUNCT_RE.sub("", title_core.lower()).split()
    if core_tokens and slug_tokens[: len(core_tokens)] == core_tokens:
        rest = slug_tokens[len(core_tokens) :]
        rest_text = " ".join(rest)
        if rest and _MIX_MARKER_RE.search(rest_text):
            return rest_text
    return ""


def _hit_mix_blob(hit: StoreHit) -> str:
    return f"{hit.mix_label} {hit.slug_text}".lower().replace("-", " ")


def _is_original_hit(hit: StoreHit) -> bool:
    if hit.mix_kind == "original":
        return True
    blob = _hit_mix_blob(hit)
    return "original mix" in blob


def _is_remix_hit(hit: StoreHit) -> bool:
    if hit.mix_kind == "remix":
        return True
    return "remix" in _hit_mix_blob(hit)


def _is_no_mix_hit(hit: StoreHit) -> bool:
    return not _is_remix_hit(hit) and not _is_original_hit(hit) and not hit.mix_label


def _remixer_overlap(query: StoreQuery, hit: StoreHit) -> bool:
    if not query.remixers:
        return False
    haystacks = [
        *hit.remixers,
        *hit.artists,
        hit.mix_label,
        hit.slug_text.replace("-", " "),
    ]
    for remixer in query.remixers:
        for hay in haystacks:
            if hay and fuzz.partial_ratio(remixer, hay) >= REMIXER_OVERLAP:
                return True
    return False


def _artist_score(query: StoreQuery, hit: StoreHit) -> int | None:
    performers = [name for name in (*hit.artists, *hit.remixers) if name]
    if not query.artists or not performers:
        return None
    return fuzz.token_set_ratio(" ".join(query.artists), " ".join(performers))


def _normalize_isrc(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().casefold()


def _isrcs_match(left: str | None, right: str | None) -> bool:
    a = _normalize_isrc(left)
    b = _normalize_isrc(right)
    return bool(a) and a == b
