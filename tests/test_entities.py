"""Dynamic check entity tests."""

from __future__ import annotations

from typing import Any

from custom_components.ha_watcher.binary_sensor import (
    WatcherCheckSensor,
    WatcherOutageSensor,
)
from custom_components.ha_watcher.sensor import PAYLOAD_SENSORS, WatcherPayloadSensor
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .helpers import entity_id


async def test_new_checks_create_entities(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    """A check name seen for the first time gets its own entity."""
    await post(payload)
    registry = er.async_get(hass)
    before = len(er.async_entries_for_config_entry(registry, "entry1"))

    payload["checks"]["entity:sensor.example_temperature"] = {
        "ok": False,
        "since": "2026-10-13T11:59:00+00:00",
        "detail": "sensor.example_temperature has been unavailable for 2h",
        "failures": 1,
    }
    await post(payload)
    after = len(er.async_entries_for_config_entry(registry, "entry1"))
    assert after == before + 1
    state = hass.states.get(
        entity_id(hass, "binary_sensor", "check_entity:sensor.example_temperature")
    )
    assert state.state == STATE_ON
    assert state.attributes["check"] == "entity:sensor.example_temperature"

    # Sending the same set again does not add duplicates.
    await post(payload)
    assert len(er.async_entries_for_config_entry(registry, "entry1")) == after


async def test_vanished_check_becomes_unavailable(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    """A check missing from the latest report is shown as unavailable."""
    await post(payload)
    del payload["checks"]["backup"]
    await post(payload)
    backup = hass.states.get(entity_id(hass, "binary_sensor", "check_backup"))
    assert backup.state == STATE_UNAVAILABLE
    api = hass.states.get(entity_id(hass, "binary_sensor", "check_api"))
    assert api.state == STATE_OFF


async def test_outage_sensor(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    """The outage sensor follows the watcher's outage flag."""
    payload["outage"] = {"active": True, "since": "2026-10-13T11:50:00Z"}
    await post(payload)
    state = hass.states.get(entity_id(hass, "binary_sensor", "outage"))
    assert state.state == STATE_ON
    assert state.attributes["since"] == "2026-10-13T11:50:00+00:00"


async def test_payload_entities_unavailable_before_first_report(
    hass: HomeAssistant, setup_entry
) -> None:
    """Before any report, payload sensors are unavailable."""
    for platform, key in (("binary_sensor", "outage"), ("sensor", "alerts_sent_24h")):
        state = hass.states.get(entity_id(hass, platform, key))
        assert state.state == STATE_UNAVAILABLE
    last = hass.states.get(entity_id(hass, "sensor", "last_report"))
    assert last.state == "unknown"
    hub = setup_entry.runtime_data
    assert hub.check("api") is None
    assert hub.check_names == []


async def test_entity_values_without_payload(hass: HomeAssistant, setup_entry) -> None:
    """Entities return empty values when asked directly before any report."""
    hub = setup_entry.runtime_data
    outage = WatcherOutageSensor(hub)
    assert outage.is_on is None
    assert outage.extra_state_attributes is None
    check = WatcherCheckSensor(hub, "api")
    assert check.is_on is None
    assert check.extra_state_attributes is None
    assert check.available is False
    sensor = WatcherPayloadSensor(hub, PAYLOAD_SENSORS[0])
    assert sensor.native_value is None
    # A timeout firing while already offline changes nothing.
    hub._cancel_timer()
    hub.online = False
    hub._handle_timeout(None)
    assert hub.online is False
