"""Dynamic check entity tests."""

from __future__ import annotations

from typing import Any

import pytest

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

    payload["checks"]["entity_sensor_example_temperature"] = {
        "ok": False,
        "since": "2026-10-13T11:59:00+00:00",
        "detail": "sensor.example_temperature has been unavailable for 2h",
        "failures": 1,
    }
    await post(payload)
    after = len(er.async_entries_for_config_entry(registry, "entry1"))
    assert after == before + 1
    state = hass.states.get(
        entity_id(hass, "binary_sensor", "check_entity_sensor_example_temperature")
    )
    assert state.state == STATE_ON
    assert state.attributes["check"] == "entity_sensor_example_temperature"

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
    assert hub.known_checks == []


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


def _check(ok: bool = True) -> dict[str, Any]:
    return {"ok": ok, "since": None, "detail": "", "failures": 0}


def _check_entities(hass: HomeAssistant) -> list[str]:
    registry = er.async_get(hass)
    return [
        e.unique_id
        for e in er.async_entries_for_config_entry(registry, "entry1")
        if e.unique_id.startswith("entry1_check_")
    ]


async def test_invalid_check_names_ignored(
    hass: HomeAssistant,
    post,
    payload: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Names outside [a-z0-9_]{1,40} are dropped (once-logged); the rest is used."""
    payload["checks"]["Bad Name"] = _check()
    payload["checks"]["core:safe_mode"] = _check()
    payload["checks"]["x" * 41] = _check()
    payload["checks"]["y" * 40] = _check()
    resp = await post(payload)
    assert resp.status == 200
    await post(payload)
    names = sorted(_check_entities(hass))
    assert names == sorted(
        f"entry1_check_{n}" for n in ("api", "backup", "host", "y" * 40)
    )
    assert caplog.text.count("Ignored 3 check(s) with invalid names") == 1
    assert "Bad Name" not in caplog.text
    hub = hass.config_entries.async_get_entry("entry1").runtime_data
    assert "core:safe_mode" not in hub.payload["checks"]


async def test_lifetime_check_cap_survives_reload(
    hass: HomeAssistant,
    post,
    payload: dict[str, Any],
    setup_entry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Rotating names cannot create more than 32 check entities, even across restarts."""
    payload["checks"] = {f"a{i}": _check() for i in range(20)}
    await post(payload)
    payload["checks"] = {f"b{i}": _check() for i in range(20)}
    await post(payload)
    assert len(_check_entities(hass)) == 32
    assert caplog.text.count("check limit of 32 reached") == 1
    hub = setup_entry.runtime_data
    # Known checks still report; names past the cap are not stored.
    assert sorted(hub.payload["checks"]) == sorted(f"b{i}" for i in range(12))

    await post(payload)
    assert caplog.text.count("check limit of 32 reached") == 1

    assert await hass.config_entries.async_reload(setup_entry.entry_id)
    await hass.async_block_till_done()
    hub = setup_entry.runtime_data
    assert len(hub.known_checks) == 32
    payload["checks"] = {"c0": _check()}
    await post(payload)
    assert len(_check_entities(hass)) == 32
    assert hub.payload["checks"] == {}
    # A known name is still accepted after the reload.
    payload["checks"] = {"a0": _check(False)}
    await post(payload)
    state = hass.states.get(entity_id(hass, "binary_sensor", "check_a0"))
    assert state.state == STATE_ON
