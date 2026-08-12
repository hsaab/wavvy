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

import httpx
from bs4 import BeautifulSoup

from beatport_browser import BeatportBrowser, BeatportBrowserError
from database import get_supabase, update_track_fields
from store_match import StoreQuery, parse_store_hit, parse_store_query, score_hit
from traxsource_browser import TraxsourceBrowser, TraxsourceBrowserError
from ws_manager import manager

# Statuses whose tracks still benefit from (re-)resolution. Anything past
# `cart_failed` is either already resolved or past the point where a link would
# change the outcome.
RESOLVABLE_STATUSES = ("new", "approved", "cart_failed")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ODESLI_API = "https://api.song.link/v1-alpha.1/links"

# Odesli's free tier rate-limits aggressively (HTTP 429). We enforce a global
# minimum spacing between calls and honor ``Retry-After`` so a single batch
# doesn't stampede the endpoint. The previous implementation retried within
# milliseconds, which burned the rate budget and produced a 429 on nearly
# every track.
ODESLI_MIN_INTERVAL_SECS = 2.0
ODESLI_MAX_RETRIES = 2
ODESLI_MAX_BACKOFF_SECS = 20.0

HIGH_CONFIDENCE = 90
MEDIUM_CONFIDENCE = 75
# Floor for *fallback* matches (Beatport / Traxsource scraping). Anything
# below this is almost certainly a wrong match — e.g. a promoted track from
# a homepage fallback — so we drop it rather than persist a misleading link.
# Odesli matches are unaffected and keep their 100-baseline.
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


def _store_query(title: str, artist: str) -> StoreQuery:
    """Parse source identity once per search page."""
    return parse_store_query(artist=artist, title=title)


def _score_store_row(query: StoreQuery, title: str, artist: str, url: str) -> int:
    """Score a candidate through store_match (title parse only in this slice)."""
    hit = parse_store_hit(title=title, artist=artist, url=url)
    return score_hit(query, hit)


# ---------------------------------------------------------------------------
# Odesli (song.link) lookup
# ---------------------------------------------------------------------------

# Global throttle state shared across the batch. The resolver runs one track
# at a time on a single event loop, so a lock plus a last-call timestamp is
# enough to guarantee a minimum spacing between Odesli requests.
_odesli_throttle_lock = asyncio.Lock()
_odesli_last_call_at = 0.0


async def _odesli_throttle() -> None:
    """Block until at least :data:`ODESLI_MIN_INTERVAL_SECS` has elapsed
    since the previous Odesli request."""
    global _odesli_last_call_at
    async with _odesli_throttle_lock:
        loop = asyncio.get_event_loop()
        elapsed = loop.time() - _odesli_last_call_at
        if elapsed < ODESLI_MIN_INTERVAL_SECS:
            await asyncio.sleep(ODESLI_MIN_INTERVAL_SECS - elapsed)
        _odesli_last_call_at = loop.time()


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """Parse a ``Retry-After`` header in delta-seconds form, if present."""
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


async def _odesli_lookup(
    client: httpx.AsyncClient,
    spotify_id: str,
) -> dict[str, str]:
    """Query Odesli for Beatport / Traxsource URLs from a Spotify track ID.

    Returns a dict that may contain ``beatport_url`` and/or ``traxsource_url``.
    Honors ``Retry-After`` on 429 and gives up gracefully after a few
    attempts so the scraping fallbacks can take over rather than stalling
    the whole batch.
    """
    spotify_url = f"https://open.spotify.com/track/{spotify_id}"
    params = {"url": spotify_url, "userCountry": "US"}

    for attempt in range(ODESLI_MAX_RETRIES + 1):
        await _odesli_throttle()
        try:
            resp = await client.request(
                "GET", ODESLI_API, params=params, timeout=15.0,
            )
        except httpx.RequestError as exc:
            logger.warning("Odesli request error for %s: %s", spotify_id, exc)
            return {}

        if resp.status_code == 429:
            if attempt >= ODESLI_MAX_RETRIES:
                logger.warning(
                    "Odesli rate-limited for %s after %d attempt(s); "
                    "falling back to scraping", spotify_id, attempt + 1,
                )
                return {}
            backoff = _retry_after_seconds(resp) or (2.0**attempt) * ODESLI_MIN_INTERVAL_SECS
            backoff = min(backoff, ODESLI_MAX_BACKOFF_SECS)
            logger.info(
                "Odesli 429 for %s; backing off %.1fs (retry %d/%d)",
                spotify_id, backoff, attempt + 1, ODESLI_MAX_RETRIES,
            )
            await asyncio.sleep(backoff)
            continue

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("Odesli lookup failed for %s: %s", spotify_id, exc)
            return {}

        try:
            platforms = resp.json().get("linksByPlatform", {})
        except ValueError as exc:
            logger.warning("Odesli returned non-JSON for %s: %s", spotify_id, exc)
            return {}

        links: dict[str, str] = {}
        if "beatport" in platforms:
            links["beatport_url"] = platforms["beatport"]["url"]
        if "traxsource" in platforms:
            links["traxsource_url"] = platforms["traxsource"]["url"]
        return links

    return {}


# ---------------------------------------------------------------------------
# Beatport search fallback
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
) -> tuple[str | None, int]:
    """Score each track hit and return the best ``(url, score)`` pair."""
    query = _store_query(target_title, target_artist)
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
        score = _score_store_row(query, raw_name, artist_str, url)
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
        score = _score_store_row(query, raw_name, artist_str, url)
        if score > best_score:
            best_score = score
            best_url = url

    return best_url, best_score


def _parse_beatport_next_data(
    html: str,
    target_title: str,
    target_artist: str,
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
        tracks, target_title, target_artist,
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
        score = _score_store_row(query, title_text, artist_text, url)
        if score > best_score:
            best_score = score
            best_url = url

    return best_url, best_score


async def _beatport_search(
    bp_browser: BeatportBrowser,
    title: str,
    artist: str,
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

    bp_url, score = _parse_beatport_next_data(html, title, artist)
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
# Traxsource search fallback
# ---------------------------------------------------------------------------

def _absolutize_traxsource(href: str) -> str:
    """Turn a relative Traxsource href into an absolute URL."""
    return href if href.startswith("http") else f"https://www.traxsource.com{href}"


def _parse_traxsource_html(
    html: str,
    query: StoreQuery,
) -> tuple[str | None, int]:
    """Extract the best Traxsource track URL from a search-results page.

    Tries structured ``.trk-row`` rows first (title + artist cells, the
    reliable path), then falls back to scanning generic ``/track/`` and
    ``/title/`` anchors with whatever artist context is nearby. Slug text
    from the URL is title and mix evidence; it is never treated as artist
    credits.
    """
    soup = BeautifulSoup(html, "html.parser")
    best_url: str | None = None
    best_score = 0
    best_rejected_score = 0
    best_rejected_reason = "no candidates"
    best_rejected_url: str | None = None

    def consider(title: str, artist: str, href: str) -> None:
        nonlocal best_url, best_score
        nonlocal best_rejected_score, best_rejected_reason, best_rejected_url
        if not href:
            return
        url = _absolutize_traxsource(href)
        score = _score_store_row(query, title, artist, url)
        if score >= MIN_FALLBACK_SCORE and score > best_score:
            best_score = score
            best_url = url
            return
        if score > best_rejected_score:
            best_rejected_score = score
            best_rejected_url = url
            if score == 0:
                best_rejected_reason = "remix gate or incompatible mix"
            else:
                best_rejected_reason = "below floor"

    # Preferred: structured track rows with separate title + artist cells.
    for row in soup.select(".trk-row, .search-trk-row")[:15]:
        title_el = row.select_one(".trk-cell.title a, .title a")
        if not title_el:
            continue
        artist_el = row.select_one(
            ".trk-cell.artists a, .artists a, a[href*='/artist/']"
        )
        row_title = title_el.get_text(" ", strip=True)
        row_artist = artist_el.get_text(" ", strip=True) if artist_el else ""
        consider(row_title, row_artist, title_el.get("href", ""))

    if best_url:
        return best_url, best_score

    # Fallback: generic track links without structured row context.
    for link in soup.select("a[href*='/track/'], a[href*='/title/']")[:15]:
        text = link.get_text(" ", strip=True)
        if not text:
            continue

        link_artist = ""
        parent = link.find_parent(["div", "li", "tr"])
        if parent:
            artist_el = parent.select_one("a[href*='/artist/']")
            if artist_el:
                link_artist = artist_el.get_text(" ", strip=True)

        consider(text, link_artist, link.get("href", ""))

    if not best_url:
        logger.info(
            "Traxsource best rejected score %d (%s) for candidate %s",
            best_rejected_score, best_rejected_reason, best_rejected_url,
        )
        return None, 0

    return best_url, best_score


async def _traxsource_search(
    ts_browser: TraxsourceBrowser,
    title: str,
    artist: str,
) -> tuple[str | None, int]:
    """Search Traxsource for *artist — title*. Returns (url, fuzzy_score).

    Fetches the search page through a real Chromium session (``ts_browser``)
    because Traxsource now sits behind Cloudflare's JS challenge — plain
    HTTP requests receive a 403 interstitial. Callers own the
    :class:`TraxsourceBrowser` lifecycle (one per batch).

    Matches below :data:`MIN_FALLBACK_SCORE` are dropped in favor of
    reporting ``not_found``. Raises :class:`TraxsourceBrowserError` on a
    browser/session failure; the caller decides how to handle it.
    """
    html = await ts_browser.search(title, artist)

    ts_url, score = _parse_traxsource_html(
        html, parse_store_query(artist=artist, title=title),
    )

    if ts_url and score < MIN_FALLBACK_SCORE:
        logger.info(
            "Traxsource fallback rejected for '%s - %s': best score %d "
            "below floor %d (candidate: %s)",
            artist, title, score, MIN_FALLBACK_SCORE, ts_url,
        )
        return None, 0

    return ts_url, score


# ---------------------------------------------------------------------------
# Single-track resolver
# ---------------------------------------------------------------------------

async def resolve_track(
    track: dict,
    client: httpx.AsyncClient,
    bp_browser: BeatportBrowser | None = None,
    ts_browser: TraxsourceBrowser | None = None,
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

    ``bp_browser`` / ``ts_browser`` must be provided to enable the Beatport /
    Traxsource fallbacks respectively; without them, only Odesli can supply
    those links. Callers construct one browser of each per batch and share
    them across tracks.
    """
    title = track.get("track_name", "")
    artist = track.get("artist_name", "")
    spotify_id = track.get("spotify_id", "")

    result: dict = {
        "beatport_url": None,
        "traxsource_url": None,
        "match_confidence": "not_found",
        "confidence_score": 0,
        "errors": [],
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

    # --- Step 2: Beatport scraping fallback (via Playwright) ---
    if not result["beatport_url"] and title and artist:
        if bp_browser is None:
            result["errors"].append("beatport: browser session unavailable")
        else:
            await asyncio.sleep(SCRAPE_DELAY_SECS)
            try:
                bp_url, bp_score = await _beatport_search(bp_browser, title, artist)
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

    # --- Step 3: Traxsource scraping fallback (via Playwright) ---
    if not result["traxsource_url"] and title and artist:
        if ts_browser is None:
            result["errors"].append("traxsource: browser session unavailable")
        else:
            await asyncio.sleep(SCRAPE_DELAY_SECS)
            try:
                ts_url, ts_score = await _traxsource_search(ts_browser, title, artist)
                if ts_url:
                    result["traxsource_url"] = ts_url
                    best_score = max(best_score, ts_score)
            except TraxsourceBrowserError as exc:
                # Per-search fresh contexts make failures independent, so a
                # blocked search doesn't doom the rest of the batch — log it
                # and move on rather than short-circuiting like Beatport.
                logger.warning(
                    "Traxsource browser error for '%s - %s': %s",
                    artist, title, exc,
                )
                result["errors"].append(f"traxsource: {exc}")
            except Exception as exc:
                logger.warning(
                    "Traxsource search failed for '%s - %s': %s",
                    artist, title, exc,
                )
                result["errors"].append(f"traxsource: {exc}")

    if result["beatport_url"] or result["traxsource_url"]:
        result["confidence_score"] = best_score
        result["match_confidence"] = _classify_confidence(best_score)

    return result


# ---------------------------------------------------------------------------
# Batch resolver (called by the /api/resolve endpoint)
# ---------------------------------------------------------------------------

def _fetch_tracks_needing_resolution() -> list[dict]:
    """Default batch target: active tracks missing at least one store URL.

    Previously limited to ``status = 'new'`` which silently skipped any track
    that had been approved or cart-failed before resolution ever ran. We now
    include all statuses where a link could still affect downstream actions.
    """
    return (
        get_supabase()
        .table("tracks")
        .select("*")
        .in_("status", list(RESOLVABLE_STATUSES))
        .or_("beatport_url.is.null,traxsource_url.is.null")
        .execute()
        .data
    )


# Single-flight guard. Two callers can fire the auto-resolve background task
# nearly simultaneously (e.g. /api/scan and a playlist sync), which previously
# doubled every outbound request and tipped Odesli over its rate limit. A
# duplicate *auto* batch (track_ids is None) is skipped outright; explicit
# track_id batches serialize on the lock so their specific tracks still get
# resolved once the running batch finishes.
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
    still missing a Beatport or Traxsource URL.

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
    ts_browser = TraxsourceBrowser()
    try:
        async with httpx.AsyncClient() as client:
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
                        client,
                        bp_browser if not batch_error else None,
                        ts_browser,
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
                    # tracks but keep going so Odesli/Traxsource results still land.
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
        await ts_browser.close()

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
