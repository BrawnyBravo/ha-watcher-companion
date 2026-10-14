"""Binary sensors for HA Watcher."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import WatcherEntity, WatcherPayloadEntity
from .hub import HAWatcherConfigEntry, WatcherHub

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HAWatcherConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors, adding a new one whenever a new check appears."""
    hub = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        WatcherOnlineSensor(hub),
        WatcherOutageSensor(hub),
    ]
    entities.extend(WatcherCheckSensor(hub, name) for name in hub.known_checks)
    async_add_entities(entities)

    @callback
    def add_checks(names: list[str]) -> None:
        async_add_entities(WatcherCheckSensor(hub, name) for name in names)

    entry.async_on_unload(hub.async_add_check_listener(add_checks))


class WatcherOnlineSensor(WatcherEntity, BinarySensorEntity):
    """On while the watcher keeps reporting within the offline timeout."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "online"

    def __init__(self, hub: WatcherHub) -> None:
        super().__init__(hub, "online")

    @property
    def is_on(self) -> bool | None:
        return self.hub.online


class WatcherOutageSensor(WatcherPayloadEntity, BinarySensorEntity):
    """On while the watcher considers Home Assistant to be in an outage."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "outage"

    def __init__(self, hub: WatcherHub) -> None:
        super().__init__(hub, "outage")

    @property
    def is_on(self) -> bool | None:
        if self.hub.payload is None:
            return None
        active: bool = self.hub.payload["outage"]["active"]
        return active

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.hub.payload is None:
            return None
        return {"since": self.hub.payload["outage"]["since"]}


class WatcherCheckSensor(WatcherPayloadEntity, BinarySensorEntity):
    """One of the watcher's checks; on means the check is failing."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "check"

    def __init__(self, hub: WatcherHub, name: str) -> None:
        super().__init__(hub, f"check_{name}")
        self.check_name = name
        self._attr_translation_placeholders = {"check": name}

    @property
    def available(self) -> bool:
        return super().available and self.hub.check(self.check_name) is not None

    @property
    def is_on(self) -> bool | None:
        check = self.hub.check(self.check_name)
        if check is None or check["ok"] is None:
            return None
        return not check["ok"]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        check = self.hub.check(self.check_name)
        if check is None:
            return None
        return {
            "check": self.check_name,
            "detail": check["detail"],
            "failures": check["failures"],
            "since": check["since"],
        }
