"""Persistence tests: state must reach storage without a clean shutdown."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.ha_watcher.const import EVENT_OFFLINE, EVENT_ONLINE
from custom_components.ha_watcher.hub import WatcherHub
from homeassistant.const import STATE_OFF, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .helpers import entity_id

KEY = "ha_watcher.entry1"


def _stored(hass_storage: dict[str, Any]) -> dict[str, Any] | None:
    entry = hass_storage.get(KEY)
    return entry["data"] if entry else None


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _crash_unload(self: WatcherHub) -> None:
    """Simulate an unclean stop: release resources but write nothing."""
    self._shutdown()


async def test_saves_during_continuous_pushes(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    post,
    payload: dict[str, Any],
    hass_storage: dict[str, Any],
) -> None:
    """Reports every 30 s must not postpone the save forever."""
    for _ in range(24):  # twelve minutes of reports
        await post(payload)
        await _tick(hass, freezer, 30)
    stored = _stored(hass_storage)
    assert stored is not None
    last = dt_util.parse_datetime(stored["last_report"])
    # Routine saves land at most 300 s after the first unsaved change.
    assert dt_util.utcnow() - last <= timedelta(seconds=330)
    assert dt_util.utcnow() - last > timedelta(seconds=30)
    assert stored["payload"]["checks"]["backup"]["failures"] == 3


async def test_new_check_names_saved_immediately(
    hass: HomeAssistant, post, payload: dict[str, Any], hass_storage: dict[str, Any]
) -> None:
    """Growth of the known check set is written straight away."""
    await post(payload)
    assert _stored(hass_storage)["known_checks"] == ["api", "backup", "host"]
    payload["checks"]["memory"] = dict(payload["checks"]["api"])
    await post(payload)
    assert _stored(hass_storage)["known_checks"][-1] == "memory"


async def test_online_flag_saved_on_each_transition(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    post,
    payload: dict[str, Any],
    hass_storage: dict[str, Any],
) -> None:
    """Online/offline changes are written immediately, even under steady pushes."""
    await post(payload)
    assert _stored(hass_storage)["online"] is True
    await _tick(hass, freezer, 6 * 60)
    assert _stored(hass_storage)["online"] is False
    await post(payload)
    assert _stored(hass_storage)["online"] is True


async def test_unclean_restart_restores_state(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    post,
    payload: dict[str, Any],
    setup_entry: MockConfigEntry,
) -> None:
    """After a crash (no save on unload) the last state is still there."""
    offline = async_capture_events(hass, EVENT_OFFLINE)
    online_events = async_capture_events(hass, EVENT_ONLINE)
    await post(payload)
    await _tick(hass, freezer, 6 * 60)  # offline
    assert len(offline) == 1
    for _ in range(4):  # recovered, steady reports every 30 s
        await post(payload)
        await _tick(hass, freezer, 30)
    assert len(online_events) == 1

    with patch.object(WatcherHub, "async_unload", _crash_unload):
        assert await hass.config_entries.async_reload(setup_entry.entry_id)
        await hass.async_block_till_done()

    hub = setup_entry.runtime_data
    assert hub.known_checks == ["api", "backup", "host"]
    last = hass.states.get(entity_id(hass, "sensor", "last_report"))
    assert last.state != STATE_UNKNOWN
    # Last known state was online, so silence after the restart is an outage.
    await _tick(hass, freezer, 6 * 60)
    online = entity_id(hass, "binary_sensor", "online")
    assert hass.states.get(online).state == STATE_OFF
    assert len(offline) == 2
    assert len(online_events) == 1


async def test_urgent_save_folds_in_pending_routine_save(
    hass: HomeAssistant, post, payload: dict[str, Any], setup_entry: MockConfigEntry
) -> None:
    """An immediate save replaces a pending routine save instead of adding one."""
    hub = setup_entry.runtime_data
    await post(payload)  # first report: online changes, saved at once
    await post(payload)  # routine: one save scheduled
    assert hub._save_timer is not None
    payload["checks"]["memory"] = dict(payload["checks"]["api"])
    await post(payload)  # new check name: saved at once
    assert hub._save_timer is None


async def test_unrelated_entity_removal_keeps_known_checks(
    hass: HomeAssistant, post, payload: dict[str, Any], setup_entry: MockConfigEntry
) -> None:
    """Removing some other entity does not touch this entry's check names."""
    await post(payload)
    registry = er.async_get(hass)
    other = registry.async_get_or_create("sensor", "demo", "unrelated-1")
    registry.async_remove(other.entity_id)
    await hass.async_block_till_done()
    assert setup_entry.runtime_data.known_checks == ["api", "backup", "host"]
