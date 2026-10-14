"""Setup, unload, removal and diagnostics tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.ha_watcher.const import DOMAIN
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from .conftest import WEBHOOK_ID


async def test_unload_unregisters_webhook(
    hass: HomeAssistant, post, payload: dict[str, Any], setup_entry: MockConfigEntry
) -> None:
    """Unloading removes the webhook; later posts are ignored."""
    assert setup_entry.state is ConfigEntryState.LOADED
    await post(payload)
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert setup_entry.state is ConfigEntryState.NOT_LOADED
    assert WEBHOOK_ID not in hass.data[webhook.DOMAIN]
    resp = await post(payload)
    assert resp.status == 200  # webhook component answers 200 for unknown ids


async def test_remove_entry_deletes_storage(
    hass: HomeAssistant,
    post,
    payload: dict[str, Any],
    setup_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    """Removing the entry deletes its stored state."""
    await post(payload)
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    key = f"{DOMAIN}.{setup_entry.entry_id}"
    assert key in hass_storage
    await hass.config_entries.async_remove(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


async def test_setup_failure_unregisters_webhook(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """If forwarding to platforms fails, the webhook is cleaned up."""
    assert await async_setup_component(hass, "webhook", {})
    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        side_effect=RuntimeError("boom"),
    ):
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.SETUP_ERROR
    assert WEBHOOK_ID not in hass.data[webhook.DOMAIN]


async def test_diagnostics_redacts_webhook(
    hass: HomeAssistant,
    hass_client,
    post,
    payload: dict[str, Any],
    setup_entry: MockConfigEntry,
) -> None:
    """Diagnostics never contain the webhook id or the watcher id."""
    await post(payload)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, setup_entry)
    text = repr(diag)
    assert WEBHOOK_ID not in text
    assert "watcher-box-1" not in text
    assert diag["entry"]["data"]["webhook_id"] == "**REDACTED**"
    assert diag["runtime"]["webhook_url"].endswith("/api/webhook/**REDACTED**")
    assert diag["runtime"]["online"] is True
    assert diag["runtime"]["payload"]["checks"]["backup"]["failures"] == 3
    assert diag["entry"]["options"] == {"local_only": True, "offline_timeout": 5}


@pytest.mark.parametrize("local_only", [True, False])
async def test_webhook_registered_with_local_only(
    hass: HomeAssistant, local_only: bool
) -> None:
    """The webhook honours the local-only option and accepts POST only."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="W",
        data={"webhook_id": WEBHOOK_ID},
        options={"local_only": local_only, "offline_timeout": 5},
    )
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "webhook", {})
    assert await hass.config_entries.async_setup(entry.entry_id)
    handler = hass.data[webhook.DOMAIN][WEBHOOK_ID]
    assert handler.local_only is local_only
    assert handler.allowed_methods == frozenset({"POST"})
