"""Setup, unload, removal and diagnostics tests."""

from __future__ import annotations

import json
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
from .test_signing import sign


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
    client,
    payload: dict[str, Any],
    setup_entry: MockConfigEntry,
) -> None:
    """Diagnostics carry no webhook id, address, title, secret or check detail."""
    hass.config_entries.async_update_entry(
        setup_entry, options={**setup_entry.options, "shared_secret": "s3cret-x"}
    )
    assert await hass.config_entries.async_reload(setup_entry.entry_id)
    await hass.async_block_till_done()
    body = json.dumps(payload).encode()
    resp = await client.post(
        f"/api/webhook/{WEBHOOK_ID}", data=body, headers=sign(body, "s3cret-x")
    )
    assert resp.status == 200
    with patch(
        "homeassistant.components.webhook.get_url",
        return_value="http://192.0.2.10:8123",
    ):
        diag = await get_diagnostics_for_config_entry(hass, hass_client, setup_entry)
    text = repr(diag)
    for secret in (
        WEBHOOK_ID,
        "watcher-box-1",
        "192.0.2.10",
        "Test Watcher",
        "Newest backup is 2d old",
        "s3cret-x",
    ):
        assert secret not in text
    assert diag["entry"]["data"]["webhook_id"] == "**REDACTED**"
    assert diag["entry"]["title"] == "**REDACTED**"
    assert diag["entry"]["options"]["shared_secret"] == "**REDACTED**"
    assert diag["runtime"]["webhook_path"] == "/api/webhook/**REDACTED**"
    assert diag["runtime"]["signing_enabled"] is True
    assert diag["runtime"]["online"] is True
    backup = diag["runtime"]["payload"]["checks"]["backup"]
    assert backup["failures"] == 3
    assert backup["detail"] == "**REDACTED**"


async def test_unload_failure_still_releases_webhook(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """If a platform refuses to unload, the webhook and timer are still released."""
    hub = setup_entry.runtime_data
    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=False
    ):
        assert not await hass.config_entries.async_unload(setup_entry.entry_id)
    assert WEBHOOK_ID not in hass.data[webhook.DOMAIN]
    assert hub._timer is None


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
