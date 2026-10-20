"""Config flow and options flow tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_watcher.const import (
    CONF_LOCAL_ONLY,
    CONF_OFFLINE_TIMEOUT,
    CONF_SHARED_SECRET,
    CONF_WEBHOOK_ID,
    DOMAIN,
)
from custom_components.ha_watcher.hub import webhook_url
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers.network import NoURLAvailableError

from .conftest import WEBHOOK_ID

SECRET = "abcdefghijklmnop1234"


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
            result["flow_id"],
            {
                "name": "  Spare Pi  ",
                CONF_LOCAL_ONLY: False,
                CONF_SHARED_SECRET: f"  {SECRET}  ",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Spare Pi"
    assert result["data"] == {CONF_WEBHOOK_ID: WEBHOOK_ID}
    assert result["options"] == {
        CONF_LOCAL_ONLY: False,
        CONF_OFFLINE_TIMEOUT: 5,
        CONF_SHARED_SECRET: SECRET,
    }
    assert result["description"] == "signed"
    assert result["description_placeholders"] == {
        "webhook_url": f"http://192.0.2.10:8123/api/webhook/{WEBHOOK_ID}",
        "shared_secret": SECRET,
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
    # Cleared secret on a local-only webhook: allowed, unsigned.
    assert result["options"][CONF_SHARED_SECRET] == ""
    assert result["description"] == "default"


async def test_user_flow_offers_generated_secret(hass: HomeAssistant) -> None:
    """Setup pre-fills a random shared secret in a password field."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    key = next(k for k in result["data_schema"].schema if k == CONF_SHARED_SECRET)
    suggested = key.description["suggested_value"]
    assert len(suggested) >= 32
    assert result["data_schema"].schema[key].config["type"] == "password"
    # Another dialog gets a different secret.
    other = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    other_key = next(k for k in other["data_schema"].schema if k == CONF_SHARED_SECRET)
    assert other_key.description["suggested_value"] != suggested

    with patch("custom_components.ha_watcher.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"name": "W", CONF_LOCAL_ONLY: True, CONF_SHARED_SECRET: suggested},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_SHARED_SECRET] == suggested


@pytest.mark.parametrize(
    ("local_only", "secret", "error"),
    [
        (False, "", "secret_required"),
        (False, "   ", "secret_required"),
        (True, "short", "secret_too_short"),
        (False, " " + "x" * 15 + " ", "secret_too_short"),
    ],
)
async def test_user_flow_secret_errors(
    hass: HomeAssistant, local_only: bool, secret: str, error: str
) -> None:
    """Local-only off needs a secret; a secret needs 16 characters after trimming."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "W", CONF_LOCAL_ONLY: local_only, CONF_SHARED_SECRET: secret},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_SHARED_SECRET: error}
    # The user's choices are kept in the form.
    key = next(k for k in result["data_schema"].schema if k == CONF_LOCAL_ONLY)
    assert key.default() is local_only


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
        result["flow_id"],
        {CONF_OFFLINE_TIMEOUT: 10.0, CONF_LOCAL_ONLY: False, "shared_secret": SECRET},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_entry.options == {
        CONF_OFFLINE_TIMEOUT: 10,
        CONF_LOCAL_ONLY: False,
        "shared_secret": SECRET,
    }
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


async def test_options_flow_shared_secret(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """A shared secret can be set and cleared again."""
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_OFFLINE_TIMEOUT: 5,
            CONF_LOCAL_ONLY: True,
            "shared_secret": "  abcdefghijklmnop1234  ",
        },
    )
    await hass.async_block_till_done()
    assert setup_entry.options["shared_secret"] == "abcdefghijklmnop1234"
    assert setup_entry.runtime_data.shared_secret == "abcdefghijklmnop1234"

    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_OFFLINE_TIMEOUT: 5, CONF_LOCAL_ONLY: True}
    )
    await hass.async_block_till_done()
    assert setup_entry.options["shared_secret"] == ""
    assert setup_entry.runtime_data.shared_secret is None


async def test_options_flow_short_secret_and_suggestion(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """A suggested random secret is shown; secrets under 16 characters are refused."""
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    suggestion = result["description_placeholders"]["suggested_secret"]
    assert len(suggestion) >= 32
    schema_key = next(k for k in result["data_schema"].schema if k == "shared_secret")
    selector_cfg = result["data_schema"].schema[schema_key].config
    assert selector_cfg["type"] == "password"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_OFFLINE_TIMEOUT: 5, CONF_LOCAL_ONLY: True, "shared_secret": "short"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"shared_secret": "secret_too_short"}
    assert "shared_secret" not in setup_entry.options

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_OFFLINE_TIMEOUT: 5, CONF_LOCAL_ONLY: True, "shared_secret": suggestion},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_entry.options["shared_secret"] == suggestion


async def test_options_flow_local_only_off_needs_secret(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """Local-only cannot be turned off while signing is off."""
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_OFFLINE_TIMEOUT: 5, CONF_LOCAL_ONLY: False, "shared_secret": "  "},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"shared_secret": "secret_required"}
    assert setup_entry.options[CONF_LOCAL_ONLY] is True

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_OFFLINE_TIMEOUT: 5, CONF_LOCAL_ONLY: False}
    )
    assert result["errors"] == {"shared_secret": "secret_required"}


async def test_options_flow_does_not_prefill_secret(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """Saving other options never switches signing on by itself.

    A pre-filled secret here would lock out a watcher that does not have it yet.
    """
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    key = next(k for k in result["data_schema"].schema if k == "shared_secret")
    assert key.description["suggested_value"] == ""
