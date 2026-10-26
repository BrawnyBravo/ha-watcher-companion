"""Shared fixtures for HA Watcher tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import copy
from typing import Any

from aiohttp.test_utils import TestClient
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_watcher.const import (
    CONF_LOCAL_ONLY,
    CONF_OFFLINE_TIMEOUT,
    CONF_WEBHOOK_ID,
    DOMAIN,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

WEBHOOK_ID = "0123456789abcdef" * 4

VALID_PAYLOAD: dict[str, Any] = {
    "v": 1,
    "watcher_id": "watcher-box-1",
    "version": "1.4.0",
    "sent_at": "2026-10-13T12:00:00+00:00",
    "interval_s": 60,
    "checks": {
        "api": {
            "ok": True,
            "since": "2026-10-13T11:00:00Z",
            "detail": "API OK in 40 ms",
            "failures": 0,
        },
        "backup": {
            "ok": False,
            "since": "2026-10-12T03:00:00Z",
            "detail": "Newest backup is 2d old",
            "failures": 3,
        },
        "host": {"ok": None, "since": None, "detail": "not run yet", "failures": 0},
    },
    "outage": {"active": False, "since": None},
    "alerts_sent_24h": 2,
    "last_power_cycle": "2026-10-10T08:30:00Z",
    "last_recovery_command": None,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow loading custom_components in every test."""


@pytest.fixture
def payload() -> dict[str, Any]:
    """A fresh copy of a valid v1 payload."""
    return copy.deepcopy(VALID_PAYLOAD)


@pytest.fixture
def mock_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A config entry added to hass (not yet set up)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Watcher",
        data={CONF_WEBHOOK_ID: WEBHOOK_ID},
        options={CONF_LOCAL_ONLY: True, CONF_OFFLINE_TIMEOUT: 5},
        entry_id="entry1",
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> MockConfigEntry:
    """Set up the integration with the mock entry."""
    assert await async_setup_component(hass, "webhook", {})
    # Diagnostics registers HTTP views, which must happen before a test client
    # starts the server and freezes the router.
    assert await async_setup_component(hass, "diagnostics", {})
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    return mock_entry


@pytest.fixture
async def client(
    hass: HomeAssistant,
    hass_client_no_auth: Callable[[], Awaitable[TestClient]],
    setup_entry: MockConfigEntry,
) -> TestClient:
    """HTTP client for posting to the webhook."""
    return await hass_client_no_auth()


@pytest.fixture
def post(hass: HomeAssistant, client: TestClient) -> Callable[..., Awaitable[Any]]:
    """Post JSON (or raw text) to the entry's webhook and wait for HA to settle."""

    async def _post(
        body: Any = None, *, raw: str | None = None, webhook_id: str = WEBHOOK_ID
    ):
        url = f"/api/webhook/{webhook_id}"
        if raw is not None:
            resp = await client.post(
                url, data=raw, headers={"Content-Type": "application/json"}
            )
        else:
            resp = await client.post(url, json=body)
        await hass.async_block_till_done()
        return resp

    return _post
