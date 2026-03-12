"""Link resolver: finds Beatport and Traxsource URLs for Spotify tracks.

Resolution strategy (per track):
  1. Odesli API — direct platform link mapping from a Spotify URL.
  2. Beatport search fallback — scrape search results, fuzzy-match.
  3. Traxsource search fallback — same pattern, different selectors.

Each result gets a confidence score:
  high   (>= 90)  — almost certainly the same track
  medium (>= 75)  — likely match, worth reviewing
  low    (< 75)   — weak match or partial data
  not_found        — no links discovered
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from thefuzz import fuzz

from database import get_tracks_by_status, get_supabase, update_track_fields
from ws_manager import manager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ODESLI_API = "https://api.song.link/v1-alpha.1/links"

_SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

HIGH_CONFIDENCE = 90
MEDIUM_CONFIDENCE = 75
MAX_RETRIES = 3
SCRAPE_DELAY_SECS = 1.5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _classify_confidence(score: int) -> str:
    """Map a numeric fuzzy-match score to a confidence label."""
    if score >= HIGH_CONFIDENCE:
        return "high"
    if score >= MEDIUM_CONFIDENCE:
        return "medium"
    return "low"


def _normalize(text: str) -> str:
    """Strip parentheticals, brackets, punctuation — lowercase for matching."""
    text = text.lower().strip()
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _score_match(
    target_title: str,
    target_artist: str,
    candidate_title: str,
    candidate_artist: str,
) -> int:
    """Weighted fuzzy score: 60% title match + 40% artist match.

    When candidate_artist is empty, falls back to combined string comparison.
    """
    if candidate_artist:
        title_score = fuzz.token_sort_ratio(target_title, candidate_title)
        artist_score = fuzz.token_sort_ratio(target_artist, candidate_artist)
        return int(title_score * 0.6 + artist_score * 0.4)

    combined_target = f"{target_artist} {target_title}"
    return fuzz.token_sort_ratio(combined_target, candidate_title)


async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs,
) -> httpx.Response:
    """HTTP request with exponential back-off (1 s, 2 s, 4 s)."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            if attempt == MAX_RETRIES - 1:
                raise
            wait = 2**attempt
            logger.debug(
                "Retry %d/%d in %ds for %s: %s",
                attempt + 1, MAX_RETRIES, wait, url, exc,
            )
            await asyncio.sleep(wait)
    # Unreachable but keeps type-checkers happy
    raise httpx.RequestError("max retries exceeded")


# ---------------------------------------------------------------------------
# Odesli (song.link) lookup
# ---------------------------------------------------------------------------

async def _odesli_lookup(
    client: httpx.AsyncClient,
    spotify_id: str,
) -> dict[str, str]:
    """Query Odesli for Beatport / Traxsource URLs from a Spotify track ID.

    Returns a dict that may contain ``beatport_url`` and/or ``traxsource_url``.
    """
    spotify_url = f"https://open.spotify.com/track/{spotify_id}"
    try:
        resp = await _request_with_retry(
            client, "GET", ODESLI_API,
            params={"url": spotify_url, "userCountry": "US"},
            timeout=15.0,
        )
        platforms = resp.json().get("linksByPlatform", {})
        links: dict[str, str] = {}
        if "beatport" in platforms:
            links["beatport_url"] = platforms["beatport"]["url"]
        if "traxsource" in platforms:
            links["traxsource_url"] = platforms["traxsource"]["url"]
        return links
    except Exception as exc:
        logger.warning("Odesli lookup failed for %s: %s", spotify_id, exc)
        return {}


# ---------------------------------------------------------------------------
# Beatport search fallback
# ---------------------------------------------------------------------------

def _parse_beatport_next_data(
    html: str,
    target_title: str,
    target_artist: str,
) -> tuple[str | None, int]:
    """Extract track results from embedded Next.js JSON payload."""
    soup = BeautifulSoup(html, "html.parser")
    script_tag = soup.find("script", id="__NEXT_DATA__")
    if not script_tag or not script_tag.string:
        return None, 0

    try:
        data = json.loads(script_tag.string)
        props = data.get("props", {}).get("pageProps", {})

        tracks = (
            props.get("dehydratedState", {})
            .get("queries", [{}])[0]
            .get("state", {})
            .get("data", {})
            .get("tracks", {})
            .get("data", [])
        )
        if not tracks:
            tracks = props.get("tracks", {}).get("data", [])
    except (json.JSONDecodeError, IndexError, AttributeError):
        return None, 0

    best_url: str | None = None
    best_score = 0

    for t in tracks[:15]:
        name = _normalize(t.get("track_name", "") or t.get("name", ""))
        artists = _normalize(
            " ".join(
                a.get("artist_name", "") or a.get("name", "")
                for a in t.get("artists", [])
            )
        )
        score = _score_match(target_title, target_artist, name, artists)
        slug = t.get("slug", "")
        track_id = t.get("track_id", "") or t.get("id", "")
        if not slug:
            raw_name = t.get("track_name", "") or t.get("name", "")
            slug = re.sub(r"[^\w\s-]", "", raw_name.lower().strip())
            slug = re.sub(r"[\s_]+", "-", slug).strip("-")
        if slug and track_id and score > best_score:
            best_score = score
            best_url = f"https://www.beatport.com/track/{slug}/{track_id}"

    return best_url, best_score


def _parse_beatport_html(
    html: str,
    target_title: str,
    target_artist: str,
) -> tuple[str | None, int]:
    """Fallback: scan raw HTML for track links with artist context."""
    soup = BeautifulSoup(html, "html.parser")
    links = soup.select("a[href*='/track/']")
    best_url: str | None = None
    best_score = 0

    for link in links[:15]:
        title_text = _normalize(link.get_text())
        if not title_text:
            continue

        # Walk up to the parent container and look for an artist link
        artist_text = ""
        parent = link.find_parent(["div", "li", "tr", "article"])
        if parent:
            artist_link = parent.select_one("a[href*='/artist/']")
            if artist_link:
                artist_text = _normalize(artist_link.get_text())

        score = _score_match(target_title, target_artist, title_text, artist_text)
        href = link.get("href", "")
        if href and score > best_score:
            best_score = score
            best_url = href if href.startswith("http") else f"https://www.beatport.com{href}"

    return best_url, best_score


async def _beatport_search(
    client: httpx.AsyncClient,
    title: str,
    artist: str,
) -> tuple[str | None, int]:
    """Search Beatport for *artist — title*. Returns (url, fuzzy_score)."""
    query = f"{artist} {title}"
    url = f"https://www.beatport.com/search?q={quote_plus(query)}"
    norm_title = _normalize(title)
    norm_artist = _normalize(artist)

    try:
        resp = await _request_with_retry(
            client, "GET", url,
            headers=_SCRAPE_HEADERS,
            timeout=15.0,
            follow_redirects=True,
        )
        html = resp.text

        bp_url, score = _parse_beatport_next_data(html, norm_title, norm_artist)
        if bp_url:
            return bp_url, score

        return _parse_beatport_html(html, norm_title, norm_artist)

    except Exception as exc:
        logger.warning("Beatport search failed for '%s - %s': %s", artist, title, exc)
        return None, 0


# ---------------------------------------------------------------------------
# Traxsource search fallback
# ---------------------------------------------------------------------------

async def _traxsource_search(
    client: httpx.AsyncClient,
    title: str,
    artist: str,
) -> tuple[str | None, int]:
    """Search Traxsource for *artist — title*. Returns (url, fuzzy_score)."""
    query = f"{artist} {title}"
    url = f"https://www.traxsource.com/search?term={quote_plus(query)}"
    norm_title = _normalize(title)
    norm_artist = _normalize(artist)

    try:
        resp = await _request_with_retry(
            client, "GET", url,
            headers=_SCRAPE_HEADERS,
            timeout=15.0,
            follow_redirects=True,
        )
        soup = BeautifulSoup(resp.text, "html.parser")

        best_url: str | None = None
        best_score = 0

        # Prefer structured track rows with separate title + artist cells
        track_rows = soup.select(".trk-row, .search-trk-row")
        if track_rows:
            for row in track_rows[:15]:
                title_el = row.select_one(".trk-cell.title a, .title a")
                if not title_el:
                    continue
                artist_el = row.select_one(
                    ".trk-cell.artists a, .artists a, a[href*='/artist/']"
                )
                row_title = _normalize(title_el.get_text())
                row_artist = _normalize(artist_el.get_text()) if artist_el else ""

                score = _score_match(norm_title, norm_artist, row_title, row_artist)
                href = title_el.get("href", "")
                if href and score > best_score:
                    best_score = score
                    best_url = (
                        href
                        if href.startswith("http")
                        else f"https://www.traxsource.com{href}"
                    )

        # Fallback: generic /title/ links without artist context
        if not best_url:
            candidates = soup.select("a[href*='/title/']")
            combined_target = _normalize(query)
            for link in candidates[:15]:
                text = _normalize(link.get_text())
                if not text:
                    continue

                # Try to grab artist from a nearby sibling
                link_artist = ""
                parent = link.find_parent(["div", "li", "tr"])
                if parent:
                    artist_el = parent.select_one("a[href*='/artist/']")
                    if artist_el:
                        link_artist = _normalize(artist_el.get_text())

                if link_artist:
                    score = _score_match(norm_title, norm_artist, text, link_artist)
                else:
                    score = fuzz.token_sort_ratio(combined_target, text)

                href = link.get("href", "")
                if href and score > best_score:
                    best_score = score
                    best_url = (
                        href
                        if href.startswith("http")
                        else f"https://www.traxsource.com{href}"
                    )

        return best_url, best_score

    except Exception as exc:
        logger.warning(
            "Traxsource search failed for '%s - %s': %s", artist, title, exc,
        )
        return None, 0


# ---------------------------------------------------------------------------
# Single-track resolver
# ---------------------------------------------------------------------------

async def resolve_track(
    track: dict,
    client: httpx.AsyncClient,
) -> dict:
    """Resolve store links for one track.

    Returns::

        {
            "beatport_url":       str | None,
            "traxsource_url":     str | None,
            "match_confidence":   "high" | "medium" | "low" | "not_found",
            "confidence_score":   int   (0-100),
        }
    """
    title = track.get("track_name", "")
    artist = track.get("artist_name", "")
    spotify_id = track.get("spotify_id", "")

    result: dict = {
        "beatport_url": None,
        "traxsource_url": None,
        "match_confidence": "not_found",
        "confidence_score": 0,
    }

    # --- Step 1: Odesli direct lookup ---
    if spotify_id:
        odesli = await _odesli_lookup(client, spotify_id)
        result["beatport_url"] = odesli.get("beatport_url")
        result["traxsource_url"] = odesli.get("traxsource_url")

    # Both links found via Odesli — highest confidence
    if result["beatport_url"] and result["traxsource_url"]:
        result["match_confidence"] = "high"
        result["confidence_score"] = 100
        return result

    # Track the best fuzzy score across fallbacks
    # If Odesli gave one link, start from a 100-baseline for that link
    best_score = 100 if (result["beatport_url"] or result["traxsource_url"]) else 0

    # --- Step 2: Beatport scraping fallback ---
    if not result["beatport_url"] and title and artist:
        await asyncio.sleep(SCRAPE_DELAY_SECS)
        bp_url, bp_score = await _beatport_search(client, title, artist)
        if bp_url:
            result["beatport_url"] = bp_url
            best_score = max(best_score, bp_score)

    # --- Step 3: Traxsource scraping fallback ---
    if not result["traxsource_url"] and title and artist:
        await asyncio.sleep(SCRAPE_DELAY_SECS)
        ts_url, ts_score = await _traxsource_search(client, title, artist)
        if ts_url:
            result["traxsource_url"] = ts_url
            best_score = max(best_score, ts_score)

    if result["beatport_url"] or result["traxsource_url"]:
        result["confidence_score"] = best_score
        result["match_confidence"] = _classify_confidence(best_score)

    return result


# ---------------------------------------------------------------------------
# Batch resolver (called by the /api/resolve endpoint)
# ---------------------------------------------------------------------------

async def resolve_tracks(track_ids: list[int] | None = None) -> dict:
    """Resolve links for a batch of tracks.

    *track_ids*: explicit list, or ``None`` to resolve all ``new`` tracks.
    Broadcasts ``resolve_progress`` and ``resolve_complete`` via WebSocket.
    """
    if track_ids:
        rows = (
            get_supabase()
            .table("tracks")
            .select("*")
            .in_("id", track_ids)
            .execute()
        )
        tracks = rows.data
    else:
        tracks = get_tracks_by_status("new")

    if not tracks:
        await manager.broadcast("resolve_complete", {
            "resolved": 0, "total": 0, "results": [],
        })
        return {"resolved": 0, "total": 0, "results": []}

    total = len(tracks)
    results: list[dict] = []

    await manager.broadcast("resolve_progress", {
        "current": 0,
        "total": total,
        "message": f"Starting link resolution for {total} track(s)…",
    })

    async with httpx.AsyncClient() as client:
        for idx, track in enumerate(tracks, start=1):
            track_id = track["id"]
            label = f"{track.get('artist_name', '?')} — {track.get('track_name', '?')}"

            logger.info("Resolving [%d/%d]: %s", idx, total, label)
            await manager.broadcast("resolve_progress", {
                "current": idx,
                "total": total,
                "track_id": track_id,
                "message": f"Resolving {label}…",
            })

            try:
                resolved = await resolve_track(track, client)

                update_payload: dict = {
                    "match_confidence": resolved["match_confidence"],
                    "confidence_score": resolved["confidence_score"],
                }
                if resolved["beatport_url"]:
                    update_payload["beatport_url"] = resolved["beatport_url"]
                if resolved["traxsource_url"]:
                    update_payload["traxsource_url"] = resolved["traxsource_url"]

                update_track_fields(track_id, update_payload)

                results.append({"track_id": track_id, "label": label, **resolved})
            except Exception as exc:
                logger.error("Resolve failed for %s: %s", label, exc)
                results.append({
                    "track_id": track_id,
                    "label": label,
                    "error": str(exc),
                    "match_confidence": "not_found",
                })

    summary = {
        "resolved": sum(1 for r in results if r.get("match_confidence") != "not_found"),
        "total": total,
        "results": results,
    }
    await manager.broadcast("resolve_complete", summary)
    return summary
