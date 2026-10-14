"""Offline timeout, events and restore tests."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.ha_watcher.const import EVENT_OFFLINE, EVENT_ONLINE
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from .helpers import entity_id


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: float
) -> None:
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_offline_after_timeout_and_back_online(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    post,
    payload: dict[str, Any],
) -> None:
    """Silence for the timeout turns the watcher off; the next report turns it on."""
    offline_events = async_capture_events(hass, EVENT_OFFLINE)
    online_events = async_capture_events(hass, EVENT_ONLINE)
    online = entity_id(hass, "binary_sensor", "online")

    await post(payload)
    assert hass.states.get(online).state == STATE_ON
    # First report after a fresh start: no "online" event (nothing was known before).
    assert online_events == []

    await _tick(hass, freezer, 4)
    assert hass.states.get(online).state == STATE_ON
    await post(payload)  # resets the timer
    await _tick(hass, freezer, 4)
    assert hass.states.get(online).state == STATE_ON

    await _tick(hass, freezer, 1.5)
    assert hass.states.get(online).state == STATE_OFF
    assert len(offline_events) == 1
    assert offline_events[0].data["entry_id"] == "entry1"
    assert offline_events[0].data["name"] == "Test Watcher"
    assert offline_events[0].data["last_report"] is not None
    # Data from a silent watcher is stale: check entities go unavailable.
    check = entity_id(hass, "binary_sensor", "check_api")
    assert hass.states.get(check).state == STATE_UNAVAILABLE
    # Last report stays visible.
    last = hass.states.get(entity_id(hass, "sensor", "last_report"))
    assert last.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)

    # Staying silent does not fire again.
    await _tick(hass, freezer, 30)
    assert len(offline_events) == 1

    await post(payload)
    assert hass.states.get(online).state == STATE_ON
    assert len(online_events) == 1
    assert hass.states.get(check).state == STATE_OFF


async def test_never_reports_after_start(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, setup_entry: MockConfigEntry
) -> None:
    """A watcher that never reports goes from unknown to off and fires an event."""
    offline_events = async_capture_events(hass, EVENT_OFFLINE)
    online = entity_id(hass, "binary_sensor", "online")
    assert hass.states.get(online).state == STATE_UNKNOWN
    await _tick(hass, freezer, 5.5)
    assert hass.states.get(online).state == STATE_OFF
    assert len(offline_events) == 1
    assert offline_events[0].data["last_report"] is None


async def test_state_restored_after_reload(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    post,
    payload: dict[str, Any],
    setup_entry: MockConfigEntry,
) -> None:
    """After a reload the last report is restored and online starts unknown."""
    await post(payload)
    assert await hass.config_entries.async_reload(setup_entry.entry_id)
    await hass.async_block_till_done()

    online = entity_id(hass, "binary_sensor", "online")
    assert hass.states.get(online).state == STATE_UNKNOWN
    backup = hass.states.get(entity_id(hass, "binary_sensor", "check_backup"))
    assert backup.state == STATE_ON
    last = hass.states.get(entity_id(hass, "sensor", "last_report"))
    assert last.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)


async def test_offline_across_reload_no_duplicate_event(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    post,
    payload: dict[str, Any],
    setup_entry: MockConfigEntry,
) -> None:
    """Offline before a restart: no second offline event, and recovery fires online."""
    offline_events = async_capture_events(hass, EVENT_OFFLINE)
    online_events = async_capture_events(hass, EVENT_ONLINE)
    await post(payload)
    await _tick(hass, freezer, 6)
    assert len(offline_events) == 1

    assert await hass.config_entries.async_reload(setup_entry.entry_id)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 6)
    online = entity_id(hass, "binary_sensor", "online")
    assert hass.states.get(online).state == STATE_OFF
    assert len(offline_events) == 1

    assert await hass.config_entries.async_reload(setup_entry.entry_id)
    await hass.async_block_till_done()
    await post(payload)
    assert hass.states.get(online).state == STATE_ON
    assert len(online_events) == 1
