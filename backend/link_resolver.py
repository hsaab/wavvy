"""Link resolver: finds Beatport URLs for Spotify tracks.

Resolution strategy (per track):
  1. Beatport search — scrape search results, fuzzy-match.

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

from bs4 import BeautifulSoup

from beatport_browser import BeatportBrowser, BeatportBrowserError
from database import get_supabase, update_track_fields
from store_match import (
    StoreCandidate,
    StoreQuery,
    parse_store_hit,
    parse_store_query,
    score_hit,
)
from ws_manager import manager

# Statuses whose tracks still benefit from (re-)resolution. Anything past
# `cart_failed` is either already resolved or past the point where a link would
# change the outcome.
RESOLVABLE_STATUSES = ("new", "approved", "cart_failed")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HIGH_CONFIDENCE = 90
MEDIUM_CONFIDENCE = 75
# Floor for store-scrape matches (Beatport / Traxsource). Anything below
# this is almost certainly a wrong match — e.g. a promoted track from a
# homepage fallback — so we drop it rather than persist a misleading link.
MIN_FALLBACK_SCORE = 60
SCRAPE_DELAY_SECS = 1.5

# Beatport search returns its hits inside a single React-Query payload
# whose queryKey starts with "search-all". When the query has zero hits,
# Beatport falls back to rendering homepage modules (top-10 lists,
# featured releases, etc.) which look superficially similar but would
# match against random promoted tracks. Validating the query key is the
# cheapest way to tell the two apart before we run the fuzzy matcher.
_SEARCH_QUERY_KEY = "search-all"


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


def _store_query(
    title: str, artist: str, isrc: str | None = None,
) -> StoreQuery:
    """Parse source identity once per search page."""
    return parse_store_query(artist=artist, title=title, isrc=isrc)


def _score_store_row(query: StoreQuery, candidate: StoreCandidate) -> int:
    """Parse a raw store row and score it against the source identity."""
    hit = parse_store_hit(
        title=candidate.title,
        artist=candidate.artist,
        url=candidate.url,
        mix_name=candidate.mix_name,
        isrc=candidate.isrc,
    )
    return score_hit(query, hit)


# ---------------------------------------------------------------------------
# Beatport search
# ---------------------------------------------------------------------------

def _slug_from_name(name: str) -> str:
    """Build a URL-safe slug from a Beatport track/release name.

    Beatport's search payload sometimes omits the ``slug`` field. The
    canonical slug is just the lowercase name with non-word chars stripped
    and whitespace turned into hyphens — Beatport's URL handler accepts
    any reasonable slug as long as the trailing numeric ID is correct.
    """
    cleaned = re.sub(r"[^\w\s-]", "", name.lower().strip())
    return re.sub(r"[\s_]+", "-", cleaned).strip("-")


def _extract_search_data(html: str) -> dict | None:
    """Pull the ``search-all`` payload out of Beatport's ``__NEXT_DATA__``.

    Returns ``None`` if the page isn't actually a search-results page —
    e.g. Beatport's 0-results homepage fallback, where the query keys are
    ``page-modules-*`` / ``top-10-tracks`` rather than ``search-all``.
    Returning ``None`` is the resolver's signal to skip the scraping path
    entirely and avoid matching random promoted content.
    """
    soup = BeautifulSoup(html, "html.parser")
    script_tag = soup.find("script", id="__NEXT_DATA__")
    if not script_tag or not script_tag.string:
        return None

    try:
        data = json.loads(script_tag.string)
        queries = (
            data.get("props", {})
            .get("pageProps", {})
            .get("dehydratedState", {})
            .get("queries", [])
        )
    except (json.JSONDecodeError, AttributeError):
        return None

    for query in queries:
        key = query.get("queryKey")
        # queryKey shape: ["search-all", { "q": ... }, "US"]
        if isinstance(key, list) and key and key[0] == _SEARCH_QUERY_KEY:
            state_data = query.get("state", {}).get("data")
            if isinstance(state_data, dict):
                return state_data

    return None


def _best_beatport_track_match(
    tracks: list[dict],
    target_title: str,
    target_artist: str,
    isrc: str | None = None,
) -> tuple[str | None, int]:
    """Score each track hit and return the best ``(url, score)`` pair."""
    query = _store_query(target_title, target_artist, isrc=isrc)
    best_url: str | None = None
    best_score = 0

    for t in tracks[:15]:
        raw_name = t.get("track_name", "") or t.get("name", "")
        artist_str = ", ".join(
            a.get("artist_name", "") or a.get("name", "")
            for a in t.get("artists", [])
        )
        track_id = t.get("track_id") or t.get("id")
        slug = t.get("slug") or _slug_from_name(raw_name)
        if not track_id or not slug:
            continue
        url = f"https://www.beatport.com/track/{slug}/{track_id}"
        candidate = StoreCandidate(
            title=raw_name,
            artist=artist_str,
            url=url,
            mix_name=t.get("mix_name"),
            isrc=t.get("isrc"),
        )
        score = _score_store_row(query, candidate)
        if score > best_score:
            best_score = score
            best_url = url

    return best_url, best_score


def _best_beatport_release_match(
    releases: list[dict],
    target_title: str,
    target_artist: str,
) -> tuple[str | None, int]:
    """Score release hits and return the best ``(url, score)`` pair.

    Used as a fallback when no individual ``/track/`` URL is available.
    For brand-new releases Beatport sometimes indexes the release page
    before the per-track page becomes browsable, so the release URL is
    the only purchase link we can offer until indexing catches up.
    """
    query = _store_query(target_title, target_artist)
    best_url: str | None = None
    best_score = 0

    for r in releases[:15]:
        raw_name = r.get("release_name", "") or r.get("name", "")
        artist_str = ", ".join(
            a.get("artist_name", "") or a.get("name", "")
            for a in r.get("artists", [])
        )
        release_id = r.get("release_id") or r.get("id")
        slug = r.get("slug") or _slug_from_name(raw_name)
        if not release_id or not slug:
            continue
        url = f"https://www.beatport.com/release/{slug}/{release_id}"
        candidate = StoreCandidate(title=raw_name, artist=artist_str, url=url)
        score = _score_store_row(query, candidate)
        if score > best_score:
            best_score = score
            best_url = url

    return best_url, best_score


def _parse_beatport_next_data(
    html: str,
    target_title: str,
    target_artist: str,
    isrc: str | None = None,
) -> tuple[str | None, int]:
    """Extract the best Beatport URL from the search page's hydrated payload.

    Tries individual track hits first (preferred — direct add-to-cart
    target), then falls back to release hits. Returns ``(None, 0)`` when
    the page isn't a real search-results page; see :func:`_extract_search_data`.
    """
    state_data = _extract_search_data(html)
    if state_data is None:
        return None, 0

    tracks = state_data.get("tracks", {}).get("data", []) or []
    track_url, track_score = _best_beatport_track_match(
        tracks, target_title, target_artist, isrc=isrc,
    )
    if track_url:
        return track_url, track_score

    releases = state_data.get("releases", {}).get("data", []) or []
    return _best_beatport_release_match(releases, target_title, target_artist)


def _parse_beatport_html(
    html: str,
    target_title: str,
    target_artist: str,
) -> tuple[str | None, int]:
    """Last-resort raw-HTML fallback for search pages.

    Only runs when the page genuinely is a search-results page (validated
    upstream via :func:`_extract_search_data`). On a 0-results homepage
    fallback this returns ``(None, 0)`` because every ``/track/`` href on
    the page would be a promoted/unrelated track that the fuzzy matcher
    would happily but wrongly accept.
    """
    if _extract_search_data(html) is None:
        return None, 0

    query = _store_query(target_title, target_artist)
    soup = BeautifulSoup(html, "html.parser")
    links = soup.select("a[href*='/track/']")
    best_url: str | None = None
    best_score = 0

    for link in links[:15]:
        title_text = link.get_text(" ", strip=True)
        if not title_text:
            continue

        # Walk up to the parent container and look for an artist link
        artist_text = ""
        parent = link.find_parent(["div", "li", "tr", "article"])
        if parent:
            artist_link = parent.select_one("a[href*='/artist/']")
            if artist_link:
                artist_text = artist_link.get_text(" ", strip=True)

        href = link.get("href", "")
        if not href:
            continue
        url = href if href.startswith("http") else f"https://www.beatport.com{href}"
        candidate = StoreCandidate(title=title_text, artist=artist_text, url=url)
        score = _score_store_row(query, candidate)
        if score > best_score:
            best_score = score
            best_url = url

    return best_url, best_score


async def _beatport_search(
    bp_browser: BeatportBrowser,
    title: str,
    artist: str,
    isrc: str | None = None,
) -> tuple[str | None, int]:
    """Search Beatport for *artist — title*. Returns (url, fuzzy_score).

    Fetches the search page through a real Chromium session (``bp_browser``)
    because Beatport sits behind Cloudflare's JS challenge. Callers must
    construct and own the :class:`BeatportBrowser` lifecycle.

    Matches below :data:`MIN_FALLBACK_SCORE` are dropped — at that point
    we'd rather report ``not_found`` than persist a misleading link.

    Raises :class:`BeatportBrowserError` when the browser session itself
    cannot reach the page; the caller should treat that as a batch-level
    failure and avoid retrying for the rest of the batch.
    """
    html = await bp_browser.search(title, artist)

    bp_url, score = _parse_beatport_next_data(html, title, artist, isrc=isrc)
    if not bp_url:
        bp_url, score = _parse_beatport_html(html, title, artist)

    if bp_url and score < MIN_FALLBACK_SCORE:
        logger.info(
            "Beatport fallback rejected for '%s - %s': best score %d below "
            "floor %d (candidate: %s)",
            artist, title, score, MIN_FALLBACK_SCORE, bp_url,
        )
        return None, 0

    return bp_url, score


# ---------------------------------------------------------------------------
# Single-track resolver
# ---------------------------------------------------------------------------

async def resolve_track(
    track: dict,
    bp_browser: BeatportBrowser | None = None,
) -> dict:
    """Resolve store links for one track.

    Returns::

        {
            "beatport_url":       str | None,
            "traxsource_url":     str | None,
            "match_confidence":   "high" | "medium" | "low" | "not_found",
            "confidence_score":   int   (0-100),
            "errors":             list[str]   # per-source failures, user-facing
        }

    ``bp_browser`` must be provided to enable Beatport search. Callers
    construct one browser per batch and share it across tracks.
    ``traxsource_url`` stays ``None`` on this path.
    """
    title = track.get("track_name", "")
    artist = track.get("artist_name", "")
    isrc = track.get("isrc") or None

    result: dict = {
        "beatport_url": None,
        "traxsource_url": None,
        "match_confidence": "not_found",
        "confidence_score": 0,
        "errors": [],
    }

    best_score = 0

    # --- Beatport scraping (via Playwright) ---
    if title and artist:
        if bp_browser is None:
            result["errors"].append("beatport: browser session unavailable")
        else:
            await asyncio.sleep(SCRAPE_DELAY_SECS)
            try:
                bp_url, bp_score = await _beatport_search(
                    bp_browser, title, artist, isrc=isrc,
                )
                if bp_url:
                    result["beatport_url"] = bp_url
                    best_score = max(best_score, bp_score)
            except BeatportBrowserError as exc:
                # Browser-level failure: re-raise so the batch can short-circuit
                # Beatport lookups for the remaining tracks instead of retrying
                # an already-broken session.
                logger.warning(
                    "Beatport browser error for '%s - %s': %s", artist, title, exc,
                )
                raise
            except Exception as exc:
                # Parsing / unexpected failure — track it but keep batch going.
                logger.warning(
                    "Beatport search failed for '%s - %s': %s", artist, title, exc,
                )
                result["errors"].append(f"beatport: {exc}")

    if result["beatport_url"] or result["traxsource_url"]:
        result["confidence_score"] = best_score
        result["match_confidence"] = _classify_confidence(best_score)

    return result


# ---------------------------------------------------------------------------
# Batch resolver (called by the /api/resolve endpoint)
# ---------------------------------------------------------------------------

def _fetch_tracks_needing_resolution() -> list[dict]:
    """Default batch target: active tracks missing a Beatport URL.

    Previously limited to ``status = 'new'`` which silently skipped any track
    that had been approved or cart-failed before resolution ever ran. We now
    include all statuses where a link could still affect downstream actions.
    """
    return (
        get_supabase()
        .table("tracks")
        .select("*")
        .in_("status", list(RESOLVABLE_STATUSES))
        .is_("beatport_url", "null")
        .execute()
        .data
    )


# Single-flight guard. Two callers can fire the auto-resolve background task
# nearly simultaneously (e.g. /api/scan and a playlist sync), which previously
# doubled every outbound store search. A duplicate *auto* batch
# (track_ids is None) is skipped outright; explicit track_id batches
# serialize on the lock so their specific tracks still get resolved once
# the running batch finishes.
_resolve_lock = asyncio.Lock()


async def resolve_tracks(track_ids: list[int] | None = None) -> dict:
    """Resolve links for a batch of tracks, guarded against concurrent runs.

    Delegates to :func:`_run_resolve_batch` for the actual work. A second
    full auto-resolve (``track_ids is None``) that arrives while one is
    already running is skipped rather than duplicated.
    """
    if track_ids is None and _resolve_lock.locked():
        logger.info("Auto-resolve skipped: a resolve batch is already running.")
        return {"resolved": 0, "total": 0, "results": [], "skipped": True}

    async with _resolve_lock:
        return await _run_resolve_batch(track_ids)


async def _run_resolve_batch(track_ids: list[int] | None = None) -> dict:
    """Resolve links for a batch of tracks.

    *track_ids*: explicit list, or ``None`` to resolve every active track
    still missing a Beatport URL.

    Broadcasts ``resolve_progress`` and ``resolve_complete`` via WebSocket.
    On browser-level failure, sends a final ``resolve_complete`` with
    ``batch_error`` set so the UI can surface it.
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
        tracks = _fetch_tracks_needing_resolution()

    if not tracks:
        await manager.broadcast("resolve_complete", {
            "resolved": 0, "total": 0, "results": [],
        })
        return {"resolved": 0, "total": 0, "results": []}

    total = len(tracks)
    results: list[dict] = []
    batch_error: str | None = None

    await manager.broadcast("resolve_progress", {
        "current": 0,
        "total": total,
        "message": f"Starting link resolution for {total} track(s)…",
    })

    bp_browser = BeatportBrowser()
    try:
        for idx, track in enumerate(tracks, start=1):
            track_id = track["id"]
            label = (
                f"{track.get('artist_name', '?')} — "
                f"{track.get('track_name', '?')}"
            )

            logger.info("Resolving [%d/%d]: %s", idx, total, label)
            await manager.broadcast("resolve_progress", {
                "current": idx,
                "total": total,
                "track_id": track_id,
                "message": f"Resolving {label}…",
            })

            try:
                resolved = await resolve_track(
                    track,
                    bp_browser if not batch_error else None,
                )

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

                if resolved.get("errors"):
                    await manager.broadcast("resolve_error", {
                        "track_id": track_id,
                        "label": label,
                        "errors": resolved["errors"],
                    })

            except BeatportBrowserError as exc:
                # Browser died mid-batch — stop using it for the rest of the
                # tracks but keep going so remaining tracks still persist.
                batch_error = (
                    f"Beatport browser session failed: {exc}. "
                    f"Remaining tracks skipped Beatport fallback."
                )
                logger.error(batch_error)
                results.append({
                    "track_id": track_id,
                    "label": label,
                    "error": str(exc),
                    "match_confidence": "not_found",
                    "errors": [f"beatport: {exc}"],
                })
                await manager.broadcast("resolve_error", {
                    "track_id": track_id,
                    "label": label,
                    "batch_error": batch_error,
                })
            except Exception as exc:
                logger.error("Resolve failed for %s: %s", label, exc)
                results.append({
                    "track_id": track_id,
                    "label": label,
                    "error": str(exc),
                    "match_confidence": "not_found",
                    "errors": [str(exc)],
                })
    finally:
        await bp_browser.close()

    summary = {
        "resolved": sum(
            1 for r in results if r.get("match_confidence") != "not_found"
        ),
        "total": total,
        "results": results,
        "batch_error": batch_error,
    }
    await manager.broadcast("resolve_complete", summary)
    return summary
