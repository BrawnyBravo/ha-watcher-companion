"""HA Watcher: shows the status that an external ha-watcher service pushes in."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .hub import HAWatcherConfigEntry, WatcherHub

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: HAWatcherConfigEntry) -> bool:
    """Set up HA Watcher from a config entry."""
    hub = WatcherHub(hass, entry)
    await hub.async_setup()
    entry.runtime_data = hub
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await hub.async_unload()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HAWatcherConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # Release the webhook and timers even if a platform refused to unload, so
    # nothing is left half-alive.
    await entry.runtime_data.async_unload()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: HAWatcherConfigEntry) -> None:
    """Delete stored state when the entry is deleted."""
    hub = WatcherHub(hass, entry)
    await hub.async_remove()
