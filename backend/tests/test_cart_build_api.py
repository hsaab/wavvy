"""Cart-build API journeys: Traxsource is rejected; Beatport still starts.

These tests never hit Supabase, Playwright, or the network. They POST
/api/cart/build and GET /api/cart/status with the DB, resolver, and cart
builder patched.

Install the runner (from backend/). System Python may be PEP 668 managed, so use a venv:
    python3 -m venv ../.venv
    ../.venv/bin/pip install -r requirements-dev.txt
Then:
    ../.venv/bin/pytest tests/test_cart_build_api.py
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from main import app


def _approved_track() -> dict[str, Any]:
    return {
        "id": 11,
        "spotify_id": "sp_11",
        "track_name": "Midnight Sun",
        "artist_name": "Dazed, Nelav",
        "status": "approved",
        "beatport_url": "https://www.beatport.com/track/midnight-sun/1",
        "traxsource_url": "https://www.traxsource.com/track/1/midnight-sun",
    }


@pytest.fixture
def client() -> TestClient:
    """HTTP client that does not run app lifespan (no iTunes scan, no watcher)."""
    return TestClient(app)


@pytest.fixture
def cart_build_patched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Approved rows in memory; cart and resolve never leave the process."""
    monkeypatch.setattr(
        "database.get_tracks_by_status",
        lambda _status: [_approved_track()],
    )
    monkeypatch.setattr("link_resolver.resolve_tracks", AsyncMock(return_value={}))
    monkeypatch.setattr("cart_builder.is_running", lambda _store: False)
    monkeypatch.setattr("cart_builder.build_cart", lambda _store: {"ok": True})


def test_building_a_traxsource_cart_is_rejected(
    client: TestClient,
    cart_build_patched: None,
) -> None:
    """A user who still posts store=traxsource gets HTTP 400, not a cart job."""
    response = client.post("/api/cart/build", json={"store": "traxsource"})

    assert response.status_code == 400, (
        f"store=traxsource must be rejected; got {response.status_code} {response.json()}"
    )


def test_building_a_beatport_cart_still_starts(
    client: TestClient,
    cart_build_patched: None,
) -> None:
    """Cart BP still starts when approved tracks already have Beatport links."""
    response = client.post("/api/cart/build", json={"store": "beatport"})

    assert response.status_code == 200
    body = response.json()
    assert body.get("ok") is True


def test_cart_status_reports_beatport_only(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cart status names Beatport only; Traxsource is no longer a running store."""
    monkeypatch.setattr("cart_builder.is_running", lambda _store: False)

    response = client.get("/api/cart/status")

    assert response.status_code == 200
    payload = response.json()
    assert "beatport" in payload
    assert set(payload) == {"beatport"}
