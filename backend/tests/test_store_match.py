"""Slice 1 store-matcher journeys: mix gate, Traxsource slug, real hits in-list.

These tests never hit Beatport, Traxsource, or the network. They score canned
StoreQuery / StoreHit values through store_match.

Install the runner (from backend/):
    ../.venv/bin/pytest tests/test_store_match.py
"""

from __future__ import annotations

import inspect

from store_match import (
    StoreHit,
    StoreQuery,
    build_search_query,
    parse_store_hit,
    parse_store_query,
    score_hit,
)

# Same floor as link_resolver.MIN_FALLBACK_SCORE. Unconfirmed hits below this
# are dropped; mix-confirmed slug remixes must still clear it.
ACCEPT_FLOOR = 60

TRAXSOURCE_BOB_FOSSIL_REMIX = (
    "https://www.traxsource.com/track/6998082/bob-fossil-armen-miran-remix"
)
BEATPORT_BOB_FOSSIL_ORIGINAL = "https://www.beatport.com/track/bob-fossil/12000830"
BEATPORT_BOB_FOSSIL_REMIX = (
    "https://www.beatport.com/track/bob-fossil-armen-miran-remix/12001999"
)
BEATPORT_ECHO_ORIGINAL = "https://www.beatport.com/track/echo/10977073"
BEATPORT_ECHO_REMIX = "https://www.beatport.com/track/echo-roderic-remix/10977999"
BEATPORT_ELECTRIC_LOVE = "https://www.beatport.com/track/electric-love/999001"

GAB_RHOME_ARTISTS = "Gab Rhome, Mark Alow, Armen Miran"
BOB_FOSSIL_REMIX_TITLE = "Bob Fossil - Armen Miran Remix"


def _query(artist: str, title: str):
    return parse_store_query(artist=artist, title=title)


def _hit(title: str, artist: str, url: str) -> StoreHit:
    return parse_store_hit(title=title, artist=artist, url=url)


def _best_url(artist: str, title: str, hits: list[StoreHit]) -> str | None:
    """Pick the highest scoring hit that clears the store floor."""
    query = _query(artist, title)
    best_url: str | None = None
    best_score = 0
    for hit in hits:
        score = score_hit(query, hit)
        if score > best_score:
            best_score = score
            best_url = hit.url
    if best_score < ACCEPT_FLOOR:
        return None
    return best_url


def _score(query: StoreQuery, hit: StoreHit, isrc: str | None = None) -> int:
    """Score a hit, passing Spotify isrc when score_hit accepts that argument."""
    if isrc is not None and "isrc" in inspect.signature(score_hit).parameters:
        return score_hit(query, hit, isrc=isrc)
    return score_hit(query, hit)


def test_traxsource_bob_fossil_remix_link_is_kept_instead_of_rejected_at_score_49() -> None:
    """Empty-artist Traxsource row for 6998082 must score at least 60, not ~49."""
    query = _query(GAB_RHOME_ARTISTS, BOB_FOSSIL_REMIX_TITLE)
    # Traxsource often has the title cell but no artist cell; mix lives in the slug.
    hit = _hit("Bob Fossil", "", TRAXSOURCE_BOB_FOSSIL_REMIX)

    assert hit.artists == []
    slug = hit.slug_text.lower().replace("-", " ")
    assert "bob fossil" in slug
    assert "armen miran" in slug
    assert score_hit(query, hit) >= ACCEPT_FLOOR


def test_a_traxsource_slug_cannot_pass_on_remixer_words_alone_when_the_song_name_is_wrong() -> None:
    """A slug that only shares Armen Miran remix words is not a Bob Fossil hit."""
    query = _query(GAB_RHOME_ARTISTS, BOB_FOSSIL_REMIX_TITLE)
    hit = _hit(
        "",
        "",
        "https://www.traxsource.com/track/555/brick-lane-armen-miran-remix",
    )

    assert score_hit(query, hit) < ACCEPT_FLOOR


def test_gab_rhome_bob_fossil_binds_the_armen_miran_remix_not_the_original() -> None:
    """When both versions are in the list, Bob Fossil binds the remix, not 12000830."""
    original = _hit("Bob Fossil (Original Mix)", "Gab Rhome, Mark Alow", BEATPORT_BOB_FOSSIL_ORIGINAL)
    remix = _hit(
        "Bob Fossil (Armen Miran Remix)",
        "Gab Rhome, Mark Alow",
        BEATPORT_BOB_FOSSIL_REMIX,
    )
    query = _query(GAB_RHOME_ARTISTS, BOB_FOSSIL_REMIX_TITLE)

    assert score_hit(query, original) == 0
    assert score_hit(query, remix) >= ACCEPT_FLOOR
    assert _best_url(GAB_RHOME_ARTISTS, BOB_FOSSIL_REMIX_TITLE, [original, remix]) == remix.url


def test_gab_rhome_bob_fossil_does_not_bind_the_original_when_that_is_the_only_result() -> None:
    """A named remix must not fall back to the original just because it is the only row."""
    original = _hit("Bob Fossil (Original Mix)", "Gab Rhome, Mark Alow", BEATPORT_BOB_FOSSIL_ORIGINAL)

    assert _best_url(GAB_RHOME_ARTISTS, BOB_FOSSIL_REMIX_TITLE, [original]) is None


def test_holed_coin_echo_binds_the_roderic_remix_not_the_original() -> None:
    """When both versions are in the list, Echo binds the Roderic remix, not 10977073."""
    original = _hit("Echo (Original Mix)", "Holed Coin", BEATPORT_ECHO_ORIGINAL)
    remix = _hit("Echo (Roderic Remix)", "Holed Coin", BEATPORT_ECHO_REMIX)
    query = _query("Holed Coin", "Echo - Roderic Remix")

    assert score_hit(query, original) == 0
    assert score_hit(query, remix) >= ACCEPT_FLOOR
    assert _best_url("Holed Coin", "Echo - Roderic Remix", [original, remix]) == remix.url


def test_elfenbergs_kigelia_is_accepted_when_the_real_hit_is_already_in_the_list() -> None:
    """A plain Spotify title still matches the store row for Kigelia."""
    hit = _hit("Kigelia (Original Mix)", "Elfenberg", "https://www.beatport.com/track/kigelia/1001")

    assert _best_url("Elfenberg", "Kigelia", [hit]) == hit.url


def test_elfenbergs_kigelia_is_still_accepted_when_the_store_lists_a_remix_mix_name() -> None:
    """Unspecified titles stay matchable if the store only returned a remix mix name."""
    hit = _hit(
        "Kigelia (Be Svendsen Remix)",
        "Elfenberg",
        "https://www.beatport.com/track/kigelia/1002",
    )

    assert _best_url("Elfenberg", "Kigelia", [hit]) == hit.url


def test_hraachs_bajo_el_cielo_azul_original_mix_is_accepted_when_the_real_hit_is_already_in_the_list() -> None:
    """Hyphenated Original Mix on Spotify matches Beatport's parenthesized Original Mix."""
    original = _hit(
        "Bajo El Cielo Azul (Original Mix)",
        "Hraach",
        "https://www.beatport.com/track/bajo-el-cielo-azul/2001",
    )
    remix = _hit(
        "Bajo El Cielo Azul (Armen Miran Remix)",
        "Hraach",
        "https://www.beatport.com/track/bajo-el-cielo-azul/2002",
    )

    assert (
        _best_url("Hraach", "Bajo El Cielo Azul - Original Mix", [original, remix])
        == original.url
    )


def test_sam_shures_mirage_is_accepted_when_the_real_hit_is_already_in_the_list() -> None:
    """A plain Spotify title still matches the store row for Mirage."""
    hit = _hit("Mirage (Original Mix)", "Sam Shure", "https://www.beatport.com/track/mirage/3001")

    assert _best_url("Sam Shure", "Mirage", [hit]) == hit.url


def test_amentias_miracle_dhwange_remix_is_accepted_when_the_real_hit_is_already_in_the_list() -> None:
    """Hyphenated Armen Miran Remix matches Beatport's parenthesized remix title."""
    hit = _hit(
        "Miracle D'Hwange (Armen Miran Remix)",
        "Amentia",
        "https://www.beatport.com/track/miracle-dhwange/4001",
    )

    assert (
        _best_url("Amentia", "Miracle D'Hwange - Armen Miran Remix", [hit]) == hit.url
    )


def test_search_words_for_a_named_remix_are_the_song_name_and_the_remixer() -> None:
    """Bob Fossil searches as 'Bob Fossil Armen Miran', not the full Spotify credit blob."""
    query = _query(GAB_RHOME_ARTISTS, BOB_FOSSIL_REMIX_TITLE)

    assert build_search_query(query) == "Bob Fossil Armen Miran"


def test_search_words_for_a_plain_title_are_the_song_name_and_the_first_artist() -> None:
    """Kigelia searches as 'Kigelia Elfenberg', without stuffing mix punctuation in."""
    query = _query("Elfenberg", "Kigelia")

    assert build_search_query(query) == "Kigelia Elfenberg"


def test_a_beatport_row_with_mix_name_and_isrc_still_parses_and_keeps_those_fields() -> None:
    """Beatport mix_name and isrc stay on the hit; mix still comes from the title."""
    hit = parse_store_hit(
        title="Electric Love",
        artist="Yulia Niko",
        url=BEATPORT_ELECTRIC_LOVE,
        mix_name="Yulia Niko Remix",
        isrc="DEA002412345",
    )

    assert hit.title_core == "Electric Love"
    assert hit.url == BEATPORT_ELECTRIC_LOVE
    assert hit.mix_name == "Yulia Niko Remix"
    assert hit.isrc == "DEA002412345"
    # mix_name is attached only. Mix identity still comes from the title, not this field.
    assert hit.mix_label == ""
    assert hit.mix_kind == "unknown"


def test_a_traxsource_row_parses_with_mix_name_and_isrc_as_none() -> None:
    """Traxsource has no mix_name or isrc, so both fields are None on the hit."""
    hit = parse_store_hit(
        title="Bob Fossil",
        artist="",
        url=TRAXSOURCE_BOB_FOSSIL_REMIX,
        mix_name=None,
        isrc=None,
    )

    assert hit.mix_name is None
    assert hit.isrc is None
    assert hit.title_core == "Bob Fossil"
    assert hit.artists == []
    # Mix still comes from the slug, same as the existing Bob Fossil journey.
    assert hit.mix_kind == "remix"


def test_passing_mix_name_and_isrc_does_not_change_the_kigelia_score() -> None:
    """Optional mix_name and isrc leave the canned Kigelia score unchanged."""
    url = "https://www.beatport.com/track/kigelia/1001"
    query = _query("Elfenberg", "Kigelia")
    plain = _hit("Kigelia (Original Mix)", "Elfenberg", url)
    with_fields = parse_store_hit(
        title="Kigelia (Original Mix)",
        artist="Elfenberg",
        url=url,
        mix_name="Original Mix",
        isrc="DEKIG0000001",
    )

    assert score_hit(query, with_fields) == score_hit(query, plain)
    assert score_hit(query, with_fields) >= ACCEPT_FLOOR


def test_spotify_isrc_matching_a_beatport_hit_is_accepted_at_100_even_if_titles_differ_slightly() -> None:
    """Matching ISRCs (trim, case-insensitive) accept at 100 before fuzzy title."""
    query = _query("Holed Coin", "Echo")
    hit = parse_store_hit(
        title="Echoes",
        artist="Holed Coin",
        url=BEATPORT_ECHO_ORIGINAL,
        isrc="  gb-echo-00-00001  ",
    )

    assert _score(query, hit, isrc="GB-ECHO-00-00001") == 100
