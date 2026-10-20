"""Repair issue recommending a shared secret."""

from __future__ import annotations

from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_watcher.const import (
    CONF_LOCAL_ONLY,
    CONF_OFFLINE_TIMEOUT,
    CONF_SHARED_SECRET,
    CONF_WEBHOOK_ID,
    DOMAIN,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from .conftest import WEBHOOK_ID

SECRET = "abcdefghijklmnop1234"
ISSUE_ID = "no_shared_secret_entry1"


async def _setup(hass: HomeAssistant, **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Watcher",
        data={CONF_WEBHOOK_ID: WEBHOOK_ID},
        options={CONF_LOCAL_ONLY: True, CONF_OFFLINE_TIMEOUT: 5, **options},
        entry_id="entry1",
    )
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "webhook", {})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_unsigned_local_entry_gets_warning(hass: HomeAssistant) -> None:
    """An entry without a secret (made before setup offered one) keeps working."""
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == "no_shared_secret"
    assert issue.translation_placeholders == {"name": "Test Watcher"}
    assert issue.is_fixable is False


async def test_unsigned_remote_entry_still_works_with_error_issue(
    hass: HomeAssistant, hass_client_no_auth, payload: dict[str, Any]
) -> None:
    """Local-only off with no secret is not broken by the update, only flagged."""
    entry = await _setup(hass, **{CONF_LOCAL_ONLY: False, CONF_SHARED_SECRET: ""})
    assert entry.state is ConfigEntryState.LOADED
    client = await hass_client_no_auth()
    resp = await client.post(f"/api/webhook/{WEBHOOK_ID}", json=payload)
    assert resp.status == 200
    assert await resp.json() == {"ok": True}
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_key == "no_shared_secret_remote"


async def test_signed_entry_has_no_issue(hass: HomeAssistant) -> None:
    await _setup(hass, **{CONF_SHARED_SECRET: SECRET})
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_setting_a_secret_clears_the_issue(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_OFFLINE_TIMEOUT: 5, CONF_LOCAL_ONLY: True, CONF_SHARED_SECRET: SECRET},
    )
    await hass.async_block_till_done()
    assert entry.runtime_data.shared_secret == SECRET
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_removing_the_entry_clears_the_issue(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None
