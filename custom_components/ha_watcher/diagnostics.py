"""Diagnostics for HA Watcher (the webhook id is redacted)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_WEBHOOK_ID
from .hub import HAWatcherConfigEntry

REDACTED = "**REDACTED**"
TO_REDACT = {CONF_WEBHOOK_ID, "watcher_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HAWatcherConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    hub = entry.runtime_data
    runtime = hub.diagnostics()
    # The webhook id is the only secret protecting the endpoint; hide it in the URL too.
    runtime["webhook_url"] = runtime["webhook_url"].replace(hub.webhook_id, REDACTED)
    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "runtime": async_redact_data(runtime, TO_REDACT),
    }
