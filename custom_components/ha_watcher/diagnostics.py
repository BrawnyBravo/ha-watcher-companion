"""Diagnostics for HA Watcher (the webhook id is redacted)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_SHARED_SECRET, CONF_WEBHOOK_ID
from .hub import HAWatcherConfigEntry

REDACTED = "**REDACTED**"
# The webhook id and shared secret protect the endpoint; the watcher id, entry
# title, check names and check details often name machines, rooms or addresses.
TO_REDACT = {CONF_WEBHOOK_ID, CONF_SHARED_SECRET, "watcher_id", "detail"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HAWatcherConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    hub = entry.runtime_data
    runtime = hub.diagnostics()
    # Only the path is shown (no host or address), with the id hidden.
    runtime["webhook_path"] = runtime["webhook_path"].replace(hub.webhook_id, REDACTED)
    _anonymise_check_names(runtime)
    return {
        "entry": {
            "title": REDACTED,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "runtime": async_redact_data(runtime, TO_REDACT),
    }


def _anonymise_check_names(runtime: dict[str, Any]) -> None:
    """Replace check names with check_1, check_2, ... in place.

    Names come from watcher result keys such as entity ids and add-on slugs,
    which often name rooms, devices or machines. The same name gets the same
    placeholder in the known list and in the last report.
    """
    payload = runtime.get("payload")
    reported = list(payload["checks"]) if payload else []
    placeholders: dict[str, str] = {}
    for name in [*runtime["known_checks"], *reported]:
        placeholders.setdefault(name, f"check_{len(placeholders) + 1}")
    runtime["known_checks"] = [placeholders[n] for n in runtime["known_checks"]]
    if payload:
        runtime["payload"] = {
            **payload,
            "checks": {placeholders[n]: c for n, c in payload["checks"].items()},
        }
