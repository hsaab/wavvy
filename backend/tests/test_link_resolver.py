"""Slice 2 Beatport journeys: remixers[] and per-track HTML cards.

These tests never hit Beatport, Traxsource, or the network. They feed canned
search-all JSON and HTML into the Beatport search parser.

Install the runner (from backend/):
    ../.venv/bin/pytest tests/test_link_resolver.py
"""

from __future__ import annotations

import asyncio
import json

import link_resolver

ACCEPT_FLOOR = 60

NERVOUS_LAYERS_URL = "https://www.beatport.com/track/nervous-layers/9707615"
BRICK_LANE_URL = "https://www.beatport.com/track/brick-lane/555"
BOB_FOSSIL_REMIX_URL = "https://www.beatport.com/track/bob-fossil/12001999"
ECHO_REMIX_URL = "https://www.beatport.com/track/echo/10977999"

GAB_RHOME_ARTISTS = "Gab Rhome, Mark Alow, Armen Miran"
BOB_FOSSIL_REMIX_TITLE = "Bob Fossil - Armen Miran Remix"
HRAACH_ARTISTS = "Hraach, Armen Miran"


class _CannedBeatportBrowser:
    """Returns a fixed search-page HTML; never opens Chromium or the network."""

    def __init__(self, html: str) -> None:
        self.html = html

    async def search(self, title: str, artist: str) -> str:
        return self.html


def _beatport_search_page(*, tracks: list[dict] | None = None, body: str = "") -> str:
    """Build a search-all page. Empty tracks[] still has a search-all query key."""
    payload = {
        "props": {
            "pageProps": {
                "dehydratedState": {
                    "queries": [
                        {
                            "queryKey": ["search-all", {"q": "canned"}, "US"],
                            "state": {
                                "data": {
                                    "tracks": {"data": tracks or []},
                                    "releases": {"data": []},
                                }
                            },
                        }
                    ]
                }
            }
        }
    }
    return (
        "<html><head>"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
        f"</head><body>{body}</body></html>"
    )


def _search_beatport(html: str, title: str, artist: str) -> tuple[str | None, int]:
    return asyncio.run(
        link_resolver._beatport_search(_CannedBeatportBrowser(html), title, artist)
    )


def _messy_remix_track(
    *,
    track_name: str,
    remixer: str,
    slug: str,
    track_id: int,
    artists: list[str],
) -> dict:
    """Beatport row whose title/slug do not name the remixer; remixers[] does."""
    return {
        "track_name": track_name,
        "mix_name": "Remix",
        "slug": slug,
        "track_id": track_id,
        "artists": [{"artist_name": name} for name in artists],
        "remixers": [{"artist_name": remixer}],
    }


def _nervous_layers_track_card() -> str:
    # Artwork /track/ anchors have no visible text on Beatport; the title sits
    # on the card beside them. Scoring the anchor text alone misses the hit.
    return """
<article class="track-card">
  <a href="/track/nervous-layers/9707615"></a>
  <span class="track-title">Nervous Layers</span>
  <a href="/artist/hraach/10">Hraach</a>
  <a href="/artist/armen-miran/20">Armen Miran</a>
</article>
"""


def _brick_lane_homepage_module() -> str:
    """Promoted module whose link text overlaps the query enough to clear the floor."""
    return """
<section class="top-10-tracks">
  <a href="/track/brick-lane/555">Nervous Layers Brick Lane</a>
  <a href="/artist/hraach/10">Hraach</a>
</section>
"""


def test_beatport_remixers_confirm_armen_miran_when_the_title_only_says_remix() -> None:
    """Bob Fossil (Remix) with remixers[] Armen Miran must bind the remix row."""
    html = _beatport_search_page(
        tracks=[
            {
                "track_name": "Bob Fossil",
                "mix_name": "Original Mix",
                "slug": "bob-fossil",
                "track_id": 12000830,
                "artists": [
                    {"artist_name": "Gab Rhome"},
                    {"artist_name": "Mark Alow"},
                ],
                "remixers": [],
            },
            _messy_remix_track(
                track_name="Bob Fossil",
                remixer="Armen Miran",
                slug="bob-fossil",
                track_id=12001999,
                artists=["Gab Rhome", "Mark Alow"],
            ),
        ]
    )

    url, score = _search_beatport(html, BOB_FOSSIL_REMIX_TITLE, GAB_RHOME_ARTISTS)

    assert url == BOB_FOSSIL_REMIX_URL
    assert score >= ACCEPT_FLOOR


def test_beatport_remixers_confirm_roderic_when_the_title_only_says_remix() -> None:
    """Echo (Remix) with remixers[] Roderic must bind the remix row."""
    html = _beatport_search_page(
        tracks=[
            {
                "track_name": "Echo",
                "mix_name": "Original Mix",
                "slug": "echo",
                "track_id": 10977073,
                "artists": [{"artist_name": "Holed Coin"}],
                "remixers": [],
            },
            _messy_remix_track(
                track_name="Echo",
                remixer="Roderic",
                slug="echo",
                track_id=10977999,
                artists=["Holed Coin"],
            ),
        ]
    )

    url, score = _search_beatport(html, "Echo - Roderic Remix", "Holed Coin")

    assert url == ECHO_REMIX_URL
    assert score >= ACCEPT_FLOOR


def test_hraach_nervous_layers_matches_the_track_card_not_the_search_all_blob() -> None:
    """Empty tracks[] still has a real card; do not score the search-all blob as one hit."""
    html = _beatport_search_page(
        tracks=[],
        body=(
            '<div class="search-all">'
            '<a href="/track/brick-lane/555">Nervous Layers Brick Lane</a>'
            '<a href="/artist/hraach/10">Hraach</a>'
            f"{_nervous_layers_track_card()}"
            "</div>"
        ),
    )

    url, score = _search_beatport(html, "Nervous Layers", HRAACH_ARTISTS)

    assert url == NERVOUS_LAYERS_URL
    assert score >= ACCEPT_FLOOR
    assert url != BRICK_LANE_URL


def test_a_search_page_with_no_track_cards_does_not_match_brick_lane_from_a_homepage_module() -> None:
    """Empty tracks[] and no cards must be not_found, not a promoted Brick Lane link."""
    html = _beatport_search_page(tracks=[], body=_brick_lane_homepage_module())

    url, score = _search_beatport(html, "Nervous Layers", HRAACH_ARTISTS)

    assert url is None
    assert score == 0
