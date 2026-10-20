"""HA Watcher: shows the status that an external ha-watcher service pushes in."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .hub import HAWatcherConfigEntry, WatcherHub

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: HAWatcherConfigEntry) -> bool:
    """Set up HA Watcher from a config entry."""
    hub = WatcherHub(hass, entry)
    await hub.async_setup()
    entry.runtime_data = hub
    _async_update_secret_issue(hass, entry, hub)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await hub.async_unload()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HAWatcherConfigEntry) -> bool:
    """Unload a config entry."""
    hub = entry.runtime_data
    # Stop taking reports first: a report with a new check name must not add
    # an entity to a platform that is being torn down (for example during the
    # reload after an options change).
    hub._shutdown()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # Write the final state even if a platform refused to unload.
    await hub.async_unload()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: HAWatcherConfigEntry) -> None:
    """Delete stored state when the entry is deleted."""
    hub = WatcherHub(hass, entry)
    await hub.async_remove()
    ir.async_delete_issue(hass, DOMAIN, _secret_issue_id(entry))


def _secret_issue_id(entry: HAWatcherConfigEntry) -> str:
    return f"no_shared_secret_{entry.entry_id}"


@callback
def _async_update_secret_issue(
    hass: HomeAssistant, entry: HAWatcherConfigEntry, hub: WatcherHub
) -> None:
    """Recommend a shared secret (a repair) while reports are not signed.

    Entries made before setup offered a secret keep working unsigned; this
    only tells the user. Without local-only it is an error: anyone who learns
    the URL can then send "all OK" reports from anywhere.
    """
    issue_id = _secret_issue_id(entry)
    if hub.shared_secret is not None:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=(
            ir.IssueSeverity.WARNING if hub.local_only else ir.IssueSeverity.ERROR
        ),
        translation_key=(
            "no_shared_secret" if hub.local_only else "no_shared_secret_remote"
        ),
        translation_placeholders={"name": entry.title},
    )
