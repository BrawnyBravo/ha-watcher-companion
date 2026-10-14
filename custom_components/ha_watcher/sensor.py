"""Sensors for HA Watcher."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import WatcherEntity, WatcherPayloadEntity
from .hub import HAWatcherConfigEntry, WatcherHub
from .payload import parse_ts

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class WatcherSensorDescription(SensorEntityDescription):
    """Describes a sensor that reads one value from the last payload."""

    value_fn: Callable[[dict[str, Any]], Any]


PAYLOAD_SENSORS: tuple[WatcherSensorDescription, ...] = (
    WatcherSensorDescription(
        key="alerts_sent_24h",
        translation_key="alerts_sent_24h",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda p: p["alerts_sent_24h"],
    ),
    WatcherSensorDescription(
        key="last_power_cycle",
        translation_key="last_power_cycle",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda p: parse_ts(p["last_power_cycle"]),
    ),
    WatcherSensorDescription(
        key="last_recovery_command",
        translation_key="last_recovery_command",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda p: parse_ts(p["last_recovery_command"]),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HAWatcherConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    hub = entry.runtime_data
    entities: list[SensorEntity] = [WatcherLastReportSensor(hub)]
    entities.extend(WatcherPayloadSensor(hub, desc) for desc in PAYLOAD_SENSORS)
    async_add_entities(entities)


class WatcherLastReportSensor(WatcherEntity, SensorEntity):
    """When Home Assistant last received a valid report from the watcher."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "last_report"

    def __init__(self, hub: WatcherHub) -> None:
        super().__init__(hub, "last_report")

    @property
    def native_value(self) -> datetime | None:
        return self.hub.last_report

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.hub.payload is None:
            return None
        return {
            "sent_at": self.hub.payload["sent_at"],
            "interval_s": self.hub.payload["interval_s"],
            "watcher_version": self.hub.payload["version"],
        }


class WatcherPayloadSensor(WatcherPayloadEntity, SensorEntity):
    """A value taken from the watcher's last report."""

    entity_description: WatcherSensorDescription

    def __init__(self, hub: WatcherHub, description: WatcherSensorDescription) -> None:
        super().__init__(hub, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        if self.hub.payload is None:
            return None
        return self.entity_description.value_fn(self.hub.payload)
