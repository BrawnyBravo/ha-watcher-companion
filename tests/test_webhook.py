"""Webhook payload handling tests."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.ha_watcher.const import DOMAIN
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .conftest import WEBHOOK_ID
from .helpers import entity_id


async def test_valid_payload_updates_entities(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    """A valid report turns the watcher online and fills every entity."""
    online = entity_id(hass, "binary_sensor", "online")
    assert hass.states.get(online).state == STATE_UNKNOWN

    resp = await post(payload)
    assert resp.status == 200
    assert await resp.json() == {"ok": True}

    assert hass.states.get(online).state == STATE_ON
    assert (
        hass.states.get(entity_id(hass, "binary_sensor", "outage")).state == STATE_OFF
    )

    api = hass.states.get(entity_id(hass, "binary_sensor", "check_api"))
    assert api.state == STATE_OFF
    assert api.attributes["detail"] == "API OK in 40 ms"
    assert api.attributes["device_class"] == "problem"
    backup = hass.states.get(entity_id(hass, "binary_sensor", "check_backup"))
    assert backup.state == STATE_ON
    assert backup.attributes["failures"] == 3
    assert backup.attributes["since"] == "2026-10-12T03:00:00+00:00"
    assert backup.attributes["friendly_name"] == "Test Watcher Check backup"
    host = hass.states.get(entity_id(hass, "binary_sensor", "check_host"))
    assert host.state == STATE_UNKNOWN

    assert hass.states.get(entity_id(hass, "sensor", "alerts_sent_24h")).state == "2"
    assert (
        hass.states.get(entity_id(hass, "sensor", "last_power_cycle")).state
        == "2026-10-10T08:30:00+00:00"
    )
    assert (
        hass.states.get(entity_id(hass, "sensor", "last_recovery_command")).state
        == STATE_UNKNOWN
    )
    last = hass.states.get(entity_id(hass, "sensor", "last_report"))
    assert last.state not in (STATE_UNKNOWN, "unavailable")
    assert last.attributes["watcher_version"] == "1.4.0"
    assert last.attributes["interval_s"] == 60

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, "entry1"), "entry1"
    )
    assert device is not None
    assert device.sw_version == "1.4.0"


async def test_version_change_updates_device(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    """A new watcher version is written to the device registry."""
    await post(payload)
    payload["version"] = "2.0.0"
    await post(payload)
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, "entry1"), "entry1"
    )
    assert device.sw_version == "2.0.0"


async def test_future_version_with_extra_keys_accepted(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    """Newer contract versions and unknown keys are accepted and ignored."""
    payload["v"] = 2
    payload["new_top_level"] = {"x": 1}
    payload["checks"]["api"]["latency_ms"] = 12
    payload["outage"]["reason"] = "x"
    resp = await post(payload)
    assert resp.status == 200
    hub = hass.config_entries.async_get_entry("entry1").runtime_data
    assert "new_top_level" not in hub.payload
    assert "latency_ms" not in hub.payload["checks"]["api"]
    assert "reason" not in hub.payload["outage"]


def _mutate(payload: dict[str, Any], change: str) -> Any:
    if change == "not_object":
        return [1, 2]
    if change == "missing_key":
        del payload["interval_s"]
    elif change == "bool_as_int":
        payload["alerts_sent_24h"] = True
    elif change == "negative":
        payload["alerts_sent_24h"] = -1
    elif change == "zero_version":
        payload["v"] = 0
    elif change == "naive_time":
        payload["sent_at"] = "2026-10-13T12:00:00"
    elif change == "garbage_time":
        payload["last_power_cycle"] = "yesterday"
    elif change == "time_not_string":
        payload["sent_at"] = 12345
    elif change == "ok_not_bool":
        payload["checks"]["api"]["ok"] = "yes"
    elif change == "detail_not_string":
        payload["checks"]["api"]["detail"] = 5
    elif change == "checks_not_object":
        payload["checks"] = []
    elif change == "empty_check_name":
        payload["checks"][""] = payload["checks"]["api"]
    elif change == "too_many_checks":
        payload["checks"] = {
            f"c{i}": {"ok": True, "since": None, "detail": "", "failures": 0}
            for i in range(33)
        }
    elif change == "empty_watcher_id":
        payload["watcher_id"] = ""
    elif change == "time_overflow":
        payload["sent_at"] = "0001-01-01T00:00:00+14:00"
    elif change == "check_time_overflow":
        payload["checks"]["api"]["since"] = "9999-12-31T23:59:59-14:00"
    elif change == "huge_int":
        payload["alerts_sent_24h"] = 10**400
    elif change == "int_past_64_bit":
        payload["checks"]["api"]["failures"] = 2**64
    elif change == "interval_too_long":
        payload["interval_s"] = 86_401
    elif change == "failures_too_many":
        payload["checks"]["api"]["failures"] = 1_000_001
    elif change == "alerts_too_many":
        payload["alerts_sent_24h"] = 1_000_000_001
    elif change == "outage_missing_key":
        payload["outage"] = {"since": None}
    return payload


@pytest.mark.parametrize(
    "change",
    [
        "not_object",
        "missing_key",
        "bool_as_int",
        "negative",
        "zero_version",
        "naive_time",
        "garbage_time",
        "time_not_string",
        "ok_not_bool",
        "detail_not_string",
        "checks_not_object",
        "empty_check_name",
        "too_many_checks",
        "empty_watcher_id",
        "outage_missing_key",
        "time_overflow",
        "check_time_overflow",
        "huge_int",
        "int_past_64_bit",
        "interval_too_long",
        "failures_too_many",
        "alerts_too_many",
    ],
)
async def test_invalid_payload_rejected(
    hass: HomeAssistant, post, payload: dict[str, Any], change: str
) -> None:
    """Payloads that break the contract get a 400 and change nothing."""
    resp = await post(_mutate(payload, change))
    assert resp.status == 400
    assert await resp.json() == {"ok": False, "error": "invalid payload"}
    online = entity_id(hass, "binary_sensor", "online")
    assert hass.states.get(online).state == STATE_UNKNOWN


async def test_bad_json_rejected(hass: HomeAssistant, post) -> None:
    """A body that is not JSON gets a 400."""
    resp = await post(raw="{not json")
    assert resp.status == 400


async def test_rejection_logged_once_without_values(
    hass: HomeAssistant,
    post,
    payload: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Bad reports are logged once (no payload values) until a good one arrives."""
    caplog.set_level(logging.INFO, logger="custom_components.ha_watcher")
    bad = dict(payload)
    bad["checks"] = dict(payload["checks"])
    bad["checks"]["api"] = {**payload["checks"]["api"], "ok": "secret-token-value"}
    await post(bad)
    await post(bad)
    warnings = [
        r for r in caplog.records if "Rejected a status report" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert "secret-token-value" not in caplog.text
    assert "['checks']['api']" in warnings[0].getMessage()

    await post(payload)
    assert "valid again" in caplog.text
    await post(bad)
    warnings = [
        r for r in caplog.records if "Rejected a status report" in r.getMessage()
    ]
    assert len(warnings) == 2
    hub = hass.config_entries.async_get_entry("entry1").runtime_data
    assert hub.bad_payloads == 3


async def test_get_not_allowed(hass: HomeAssistant, client) -> None:
    """Only POST is accepted."""
    resp = await client.get(f"/api/webhook/{WEBHOOK_ID}")
    assert resp.status == 405


async def test_long_text_truncated(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    """Over-long detail text is cut to 1024 characters rather than rejected."""
    payload["checks"]["api"]["detail"] = "x" * 5000
    resp = await post(payload)
    assert resp.status == 200
    state = hass.states.get(entity_id(hass, "binary_sensor", "check_api"))
    assert len(state.attributes["detail"]) == 1024


async def test_interval_warning(
    hass: HomeAssistant,
    post,
    payload: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A report interval too close to the timeout is warned about once."""
    payload["interval_s"] = 200  # 2 x 200 s is more than the 5 minute timeout
    await post(payload)
    await post(payload)
    assert caplog.text.count("raise the timeout") == 1


async def test_deeply_nested_json_rejected(
    hass: HomeAssistant, post, caplog: pytest.LogCaptureFixture
) -> None:
    """JSON nested deep enough to blow the parser's stack gets a clean 400."""
    resp = await post(raw="[" * 200_000 + "]" * 200_000)
    assert resp.status == 400
    assert "Error processing webhook" not in caplog.text
    assert "Traceback" not in caplog.text


async def test_oversized_body_rejected(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    """Bodies over 64 KiB are refused without being parsed."""
    payload["padding"] = "x" * 70_000
    resp = await post(payload)
    assert resp.status == 400
    online = entity_id(hass, "binary_sensor", "online")
    assert hass.states.get(online).state == STATE_UNKNOWN


async def test_unexpected_error_goes_through_reject_path(
    hass: HomeAssistant, post, payload: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """Any unexpected error while validating is a 400 with a short reason only."""
    with patch(
        "custom_components.ha_watcher.hub.validate_payload",
        side_effect=OverflowError("value-from-payload"),
    ):
        resp = await post(payload)
    assert resp.status == 400
    assert "value-from-payload" not in caplog.text
    assert "Error processing webhook" not in caplog.text


async def test_oversized_chunked_body_rejected(hass: HomeAssistant, client) -> None:
    """A body without Content-Length is still cut off at 64 KiB."""

    async def chunks():
        for _ in range(20):
            yield b" " * 8192

    resp = await client.post(f"/api/webhook/{WEBHOOK_ID}", data=chunks())
    assert resp.status == 400


async def test_processing_error_contained(
    hass: HomeAssistant,
    post,
    payload: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An error after validation is answered with 500 and a short log line only."""
    with patch(
        "custom_components.ha_watcher.hub.WatcherHub._update_device_version",
        side_effect=RuntimeError("detail-from-payload"),
    ):
        resp = await post(payload)
    assert resp.status == 500
    assert await resp.json() == {"ok": False, "error": "processing failed"}
    assert "detail-from-payload" not in caplog.text
    assert "Error processing webhook" not in caplog.text
    assert "RuntimeError" in caplog.text

    # A good report re-arms the warning, so a later failure is logged again.
    payload["version"] = "1.4.1"
    assert (await post(payload)).status == 200
    with patch(
        "custom_components.ha_watcher.hub.WatcherHub._update_device_version",
        side_effect=RuntimeError("again"),
    ):
        payload["version"] = "1.4.2"
        assert (await post(payload)).status == 500
    assert caplog.text.count("Could not apply a status report") == 2


async def test_failing_listener_does_not_block_others(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    """One entity failing to update must not stop the rest."""
    hub = hass.config_entries.async_get_entry("entry1").runtime_data

    def broken() -> None:
        raise OverflowError("boom")

    hub._listeners.insert(0, broken)
    resp = await post(payload)
    assert resp.status == 200
    online = entity_id(hass, "binary_sensor", "online")
    assert hass.states.get(online).state == STATE_ON


async def test_head_answers_ok(hass: HomeAssistant, client) -> None:
    """HEAD gets 200 (documented in docs/PAYLOAD.md) and changes nothing."""
    resp = await client.head(f"/api/webhook/{WEBHOOK_ID}")
    assert resp.status == 200
    online = entity_id(hass, "binary_sensor", "online")
    assert hass.states.get(online).state == STATE_UNKNOWN


async def test_remote_request_ignored_when_local_only(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    """A report from outside the local network has no effect on a local-only webhook."""
    with patch("homeassistant.util.network.is_local", return_value=False):
        resp = await post(payload)
    assert resp.status == 200  # Home Assistant answers 200 so the URL cannot be probed
    assert await resp.text() == ""
    hub = hass.config_entries.async_get_entry("entry1").runtime_data
    assert hub.payload is None
    online = entity_id(hass, "binary_sensor", "online")
    assert hass.states.get(online).state == STATE_UNKNOWN
    # The same report from the local network is applied.
    resp = await post(payload)
    assert await resp.json() == {"ok": True}
    assert hass.states.get(online).state == STATE_ON
