"""Diagnostics for HA Watcher (the webhook id is redacted)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_SHARED_SECRET, CONF_WEBHOOK_ID
from .hub import HAWatcherConfigEntry

REDACTED = "**REDACTED**"
# The webhook id and shared secret protect the endpoint; the watcher id, entry
# title and check details often name machines or addresses on the network.
TO_REDACT = {CONF_WEBHOOK_ID, CONF_SHARED_SECRET, "watcher_id", "detail"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HAWatcherConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    hub = entry.runtime_data
    runtime = hub.diagnostics()
    # Only the path is shown (no host or address), with the id hidden.
    runtime["webhook_path"] = runtime["webhook_path"].replace(hub.webhook_id, REDACTED)
    return {
        "entry": {
            "title": REDACTED,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "runtime": async_redact_data(runtime, TO_REDACT),
    }
