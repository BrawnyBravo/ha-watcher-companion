"""Config flow and options flow tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_watcher.const import (
    CONF_LOCAL_ONLY,
    CONF_OFFLINE_TIMEOUT,
    CONF_WEBHOOK_ID,
    DOMAIN,
)
from custom_components.ha_watcher.hub import webhook_url
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers.network import NoURLAvailableError

from .conftest import WEBHOOK_ID


async def test_user_flow_creates_webhook(hass: HomeAssistant) -> None:
    """The user step creates an entry with a fresh webhook and shows its URL."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with (
        patch(
            "homeassistant.components.webhook.async_generate_id",
            return_value=WEBHOOK_ID,
        ),
        patch(
            "homeassistant.components.webhook.get_url",
            return_value="http://192.0.2.10:8123",
        ),
        patch("custom_components.ha_watcher.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"name": "  Spare Pi  ", CONF_LOCAL_ONLY: False}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Spare Pi"
    assert result["data"] == {CONF_WEBHOOK_ID: WEBHOOK_ID}
    assert result["options"] == {CONF_LOCAL_ONLY: False, CONF_OFFLINE_TIMEOUT: 5}
    assert result["description_placeholders"] == {
        "webhook_url": f"http://192.0.2.10:8123/api/webhook/{WEBHOOK_ID}"
    }


async def test_user_flow_blank_name_and_multiple_entries(hass: HomeAssistant) -> None:
    """A blank name falls back to the default; several watchers may be added."""
    MockConfigEntry(domain=DOMAIN, data={CONF_WEBHOOK_ID: "other"}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch("custom_components.ha_watcher.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"name": "   ", CONF_LOCAL_ONLY: True}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "HA Watcher"
    assert len(result["data"][CONF_WEBHOOK_ID]) == 64
    assert "/api/webhook/" in result["description_placeholders"]["webhook_url"]


async def test_webhook_url_without_configured_url(hass: HomeAssistant) -> None:
    """With no known Home Assistant URL, a placeholder address is shown."""
    with patch(
        "homeassistant.components.webhook.get_url", side_effect=NoURLAvailableError
    ):
        url = webhook_url(hass, WEBHOOK_ID)
    assert url == (
        f"http://<your-home-assistant-address>:8123/api/webhook/{WEBHOOK_ID}"
    )


async def test_options_flow(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """Options change the timeout and local-only flag and reload the entry."""
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert WEBHOOK_ID in result["description_placeholders"]["webhook_url"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_OFFLINE_TIMEOUT: 10.0, CONF_LOCAL_ONLY: False}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_entry.options == {CONF_OFFLINE_TIMEOUT: 10, CONF_LOCAL_ONLY: False}
    hub = setup_entry.runtime_data
    assert hub.local_only is False
    assert hub.timeout.total_seconds() == 600


async def test_options_flow_rejects_short_timeout(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """A timeout below the 2 minute minimum is refused."""
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_OFFLINE_TIMEOUT: 1, CONF_LOCAL_ONLY: True}
        )
