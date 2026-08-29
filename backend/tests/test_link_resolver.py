"""Slice 2 store-resolve journeys: mix_name, ISRC accept, no-hit floor.

These tests never hit Beatport, Traxsource, or the network. They score canned
Beatport search rows through _best_beatport_track_match and
_parse_beatport_next_data.

Install the runner (from backend/):
    ../.venv/bin/pytest tests/test_link_resolver.py
"""

from __future__ import annotations

import json

from link_resolver import (
    MIN_FALLBACK_SCORE,
    _best_beatport_track_match,
    _parse_beatport_next_data,
)

ELECTRIC_LOVE_ARTISTS = "Aiwaska, Starving Yet Full, Yulia Niko"
ELECTRIC_LOVE_TITLE = "Electric Love - Yulia Niko Remix"
BEATPORT_ELECTRIC_LOVE_REMIX = (
    "https://www.beatport.com/track/electric-love/29897816"
)
BEATPORT_SCUZE_ME = "https://www.beatport.com/track/scuze-me/20445501"

ELECTRIC_LOVE_ROW = {
    "track_name": "Electric Love",
    "mix_name": "Yulia Niko Remix",
    "track_id": 29897816,
    "slug": "",
    "artists": [
        {"artist_name": "Aiwaska"},
        {"artist_name": "Starving Yet Full"},
        {"artist_name": "Yulia Niko"},
    ],
    "isrc": "DEA002412345",
}
ELECTRIC_LOVE_EXTENDED_ROW = {
    "track_name": "Electric Love",
    "mix_name": "Extended Mix",
    "track_id": 29897000,
    "slug": "",
    "artists": [
        {"artist_name": "Aiwaska"},
        {"artist_name": "Starving Yet Full"},
        {"artist_name": "Yulia Niko"},
    ],
    "isrc": "DEA00EXTENDED",
}
SCUZE_ME_ROW = {
    "track_name": "Scuze Me",
    "mix_name": "Yulia Niko Remix",
    "track_id": 20445501,
    "slug": "",
    "artists": [{"artist_name": "Yulia Niko"}],
    "isrc": "DE000SCUZE01",
}

# Other Ledher catalog rows. None is Expiritualmente, and none share its ISRC.
EXPIRITUALMENTE_MISS_ROWS = [
    {
        "track_name": "Brick Lane",
        "mix_name": "Armen Miran Remix",
        "track_id": 555001,
        "slug": "brick-lane",
        "artists": [{"artist_name": "Sebastian Ledher"}],
        "isrc": "DEBRICK00001",
    },
    {
        "track_name": "Palermo Tulum",
        "mix_name": "Original Mix",
        "track_id": 555002,
        "slug": "palermo-tulum",
        "artists": [
            {"artist_name": "Sebastian Ledher"},
            {"artist_name": "Jambene"},
        ],
        "isrc": "DEPALER00002",
    },
]

ECHO_ISRC_ROW = {
    "track_name": "Echoes",
    "mix_name": None,
    "track_id": 10977073,
    "slug": "echo",
    "artists": [{"artist_name": "Holed Coin"}],
    "isrc": "  gb-echo-00-00001  ",
}


def _search_all_html(tracks: list[dict]) -> str:
    """Minimal Beatport __NEXT_DATA__ page with a search-all track list."""
    payload = {
        "props": {
            "pageProps": {
                "dehydratedState": {
                    "queries": [
                        {
                            "queryKey": ["search-all", {"q": "canned"}, "US"],
                            "state": {"data": {"tracks": {"data": tracks}}},
                        }
                    ]
                }
            }
        }
    }
    return (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(payload)}"
        "</script></html>"
    )


def _accepted_url(url: str | None, score: int) -> str | None:
    """Same drop as _beatport_search: below MIN_FALLBACK_SCORE is no URL."""
    if score < MIN_FALLBACK_SCORE:
        return None
    return url


def test_electric_love_yulia_niko_remix_binds_the_beatport_hit_with_mix_name_not_scuze_me() -> None:
    """Beatport track_name Electric Love plus mix_name is the remix, not Scuze Me."""
    tracks = [ELECTRIC_LOVE_ROW, SCUZE_ME_ROW]

    url, score = _best_beatport_track_match(
        tracks, ELECTRIC_LOVE_TITLE, ELECTRIC_LOVE_ARTISTS,
    )
    assert score >= MIN_FALLBACK_SCORE
    assert url == BEATPORT_ELECTRIC_LOVE_REMIX
    assert url != BEATPORT_SCUZE_ME

    html_url, html_score = _parse_beatport_next_data(
        _search_all_html(tracks), ELECTRIC_LOVE_TITLE, ELECTRIC_LOVE_ARTISTS,
    )
    assert html_score >= MIN_FALLBACK_SCORE
    assert html_url == BEATPORT_ELECTRIC_LOVE_REMIX
    assert html_url != BEATPORT_SCUZE_ME


def test_electric_love_remix_does_not_bind_extended_mix_listed_first() -> None:
    """Sibling version rows share the remixer as an artist and must not win at 100."""
    tracks = [ELECTRIC_LOVE_EXTENDED_ROW, ELECTRIC_LOVE_ROW]

    url, score = _best_beatport_track_match(
        tracks, ELECTRIC_LOVE_TITLE, ELECTRIC_LOVE_ARTISTS,
    )
    assert score >= MIN_FALLBACK_SCORE
    assert url == BEATPORT_ELECTRIC_LOVE_REMIX
    assert url != "https://www.beatport.com/track/electric-love/29897000"

    html_url, html_score = _parse_beatport_next_data(
        _search_all_html(tracks), ELECTRIC_LOVE_TITLE, ELECTRIC_LOVE_ARTISTS,
    )
    assert html_score >= MIN_FALLBACK_SCORE
    assert html_url == BEATPORT_ELECTRIC_LOVE_REMIX


def test_spotify_isrc_matching_a_beatport_hit_is_accepted_at_100_even_if_titles_differ_slightly() -> None:
    """Matching ISRCs (trim, case-insensitive) accept at 100 before fuzzy title."""
    url, score = _best_beatport_track_match(
        [ECHO_ISRC_ROW],
        "Echo",
        "Holed Coin",
        isrc="GB-ECHO-00-00001",
    )
    assert score == 100
    assert url == "https://www.beatport.com/track/echo/10977073"

    html_url, html_score = _parse_beatport_next_data(
        _search_all_html([ECHO_ISRC_ROW]),
        "Echo",
        "Holed Coin",
        isrc="GB-ECHO-00-00001",
    )
    assert html_score == 100
    assert html_url == "https://www.beatport.com/track/echo/10977073"


def test_expiritualmente_style_no_hit_stays_not_found_with_no_wrong_beatport_url() -> None:
    """Unrelated Ledher rows stay under 60, so no Beatport URL is kept."""
    url, score = _best_beatport_track_match(
        EXPIRITUALMENTE_MISS_ROWS,
        "Expiritualmente",
        "Sebastian Ledher, Jambene",
        isrc="USEXP0000000",
    )
    assert score < MIN_FALLBACK_SCORE
    assert _accepted_url(url, score) is None

    html_url, html_score = _parse_beatport_next_data(
        _search_all_html(EXPIRITUALMENTE_MISS_ROWS),
        "Expiritualmente",
        "Sebastian Ledher, Jambene",
        isrc="USEXP0000000",
    )
    assert html_score < MIN_FALLBACK_SCORE
    assert _accepted_url(html_url, html_score) is None
