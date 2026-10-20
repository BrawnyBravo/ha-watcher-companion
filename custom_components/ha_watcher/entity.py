"""Base entity for HA Watcher."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER, MODEL
from .hub import WatcherHub


class WatcherEntity(Entity):
    """Entity backed by a WatcherHub; updates when a report arrives or it times out."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: WatcherHub, key: str) -> None:
        self.hub = hub
        entry = hub.entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version=hub.payload["version"] if hub.payload else None,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.hub.async_add_listener(self.async_write_ha_state))


class WatcherPayloadEntity(WatcherEntity):
    """Reports values from the last payload.

    Unavailable unless a fresh report arrived in this session and the watcher
    is online: data restored after a restart may be old, and data from a
    silent watcher is stale.
    """

    @property
    def available(self) -> bool:
        return self.hub.payload is not None and self.hub.online is True
