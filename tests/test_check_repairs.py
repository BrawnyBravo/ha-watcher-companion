"""Repair issues for failing watcher checks and their fix flows."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.ha_watcher.const import CONF_CREATE_REPAIRS, DOMAIN
from custom_components.ha_watcher.repairs import (
    LEARN_MORE_URL,
    _contract_name,
    entity_id_of,
    issue_prefix,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.setup import async_setup_component

PREFIX = issue_prefix("entry1")
ENTITY = "sensor.example_temperature"
ENTITY_CHECK = "entity_sensor_example_temperature"
ENTITY_DETAIL = f"{ENTITY} has been unavailable for 2.0h"


def _check(ok: bool | None, detail: str = "detail") -> dict[str, Any]:
    return {"ok": ok, "since": None, "detail": detail, "failures": 1}


def _issue(hass: HomeAssistant, name: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{PREFIX}{name}")


def _ids(hass: HomeAssistant) -> set[str]:
    return {i for d, i in ir.async_get(hass).issues if d == DOMAIN and "check_" in i}


def _with_checks(payload: dict[str, Any], **checks: dict[str, Any]) -> dict[str, Any]:
    payload["checks"] = dict(checks)
    return payload


def _entity_payload(payload: dict[str, Any], detail: str) -> dict[str, Any]:
    return _with_checks(payload, **{ENTITY_CHECK: _check(False, detail)})


def _owner(hass: HomeAssistant) -> MockConfigEntry:
    """A made-up integration entry that owns ENTITY."""
    owner = MockConfigEntry(domain="light", title="Example Lights", entry_id="owner1")
    owner.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "sensor",
        "light",
        "uid1",
        suggested_object_id="example_temperature",
        config_entry=owner,
    )
    return owner


async def _start_fix(hass: HomeAssistant, name: str) -> dict[str, Any]:
    assert await async_setup_component(hass, "repairs", {})
    manager = hass.data["repairs"]["flow_manager"]
    return await manager.async_init(DOMAIN, context={"issue_id": f"{PREFIX}{name}"})


async def test_failing_check_creates_issue(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    await post(payload)
    assert _ids(hass) == {f"{PREFIX}backup"}  # api ok, host not run yet
    issue = _issue(hass, "backup")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == "check_failing"
    assert issue.translation_placeholders == {
        "check": "backup",
        "message": "Newest backup is 2d old",
        "watcher": "Test Watcher",
    }
    assert issue.learn_more_url == LEARN_MORE_URL
    assert issue.is_fixable is False
    assert issue.is_persistent is False


async def test_severity_error_for_api_core_host(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    await post(
        _with_checks(
            payload,
            api=_check(False),
            core=_check(False),
            host=_check(False),
            memory=_check(False),
        )
    )
    for name in ("api", "core", "host"):
        assert _issue(hass, name).severity is ir.IssueSeverity.ERROR
    assert _issue(hass, "memory").severity is ir.IssueSeverity.WARNING


async def test_update_in_place_keeps_ignore(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    await post(payload)
    ir.async_ignore_issue(hass, DOMAIN, f"{PREFIX}backup", True)
    first = _issue(hass, "backup")
    assert first.dismissed_version is not None

    payload["checks"]["backup"]["detail"] = "Newest backup is 3d old"
    await post(payload)
    second = _issue(hass, "backup")
    assert second.created == first.created
    assert second.dismissed_version == first.dismissed_version
    assert second.translation_placeholders["message"] == "Newest backup is 3d old"

    # An identical report changes nothing at all.
    await post(payload)
    assert _issue(hass, "backup") == second


async def test_deleted_on_recovery(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    await post(payload)
    payload["checks"]["backup"]["ok"] = True
    await post(payload)
    assert _ids(hass) == set()


async def test_deleted_when_check_disappears(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    await post(payload)
    del payload["checks"]["backup"]
    await post(payload)
    assert _ids(hass) == set()


async def test_not_run_yet_keeps_existing_issue(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    await post(payload)
    payload["checks"]["backup"]["ok"] = None
    await post(payload)
    assert _ids(hass) == {f"{PREFIX}backup"}


async def test_unload_removes_issues_and_reload_waits_for_report(
    hass: HomeAssistant, post, payload: dict[str, Any], setup_entry: MockConfigEntry
) -> None:
    await post(payload)
    assert _ids(hass)
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    assert _ids(hass) == set()
    assert await hass.config_entries.async_setup(setup_entry.entry_id)
    await hass.async_block_till_done()
    # The stored report is old data: nothing until a fresh report arrives.
    assert _ids(hass) == set()
    await post(payload)
    assert _ids(hass) == {f"{PREFIX}backup"}


async def test_remove_entry_removes_issues(
    hass: HomeAssistant, post, payload: dict[str, Any], setup_entry: MockConfigEntry
) -> None:
    await post(payload)
    await hass.config_entries.async_remove(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert _ids(hass) == set()


async def test_option_off_creates_none(
    hass: HomeAssistant, post, payload: dict[str, Any], setup_entry: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        setup_entry, options={**setup_entry.options, CONF_CREATE_REPAIRS: False}
    )
    await hass.async_block_till_done()
    await post(payload)
    assert _ids(hass) == set()


async def test_option_off_clears_existing(
    hass: HomeAssistant, post, payload: dict[str, Any], setup_entry: MockConfigEntry
) -> None:
    await post(payload)
    assert _ids(hass)
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"offline_timeout": 5, "local_only": True, CONF_CREATE_REPAIRS: False},
    )
    await hass.async_block_till_done()
    assert setup_entry.options[CONF_CREATE_REPAIRS] is False
    assert _ids(hass) == set()
    await post(payload)
    assert _ids(hass) == set()


def test_entity_id_of() -> None:
    assert entity_id_of(ENTITY_CHECK, ENTITY_DETAIL) == ENTITY
    assert entity_id_of(ENTITY_CHECK, f"{ENTITY} does not exist") is None
    assert entity_id_of(ENTITY_CHECK, f"Cannot read {ENTITY}: boom") is None
    # The detail must match the check name.
    assert entity_id_of("entity_sensor_other", ENTITY_DETAIL) is None
    assert entity_id_of("backup", ENTITY_DETAIL) is None
    # A shortened name (over 40 characters) puts an entity prefix in the detail.
    long_id = "sensor.a_very_long_entity_name_that_goes_on_and_on"
    name = _contract_name(long_id)
    assert len(name) == 40
    detail = f"entity:{long_id}: {long_id} has been unknown for 3.0h"
    assert entity_id_of(name, detail) == long_id


async def test_entity_issue_fixable_when_owner_known(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    _owner(hass)
    await post(_entity_payload(payload, ENTITY_DETAIL))
    issue = _issue(hass, ENTITY_CHECK)
    assert issue.is_fixable is True
    assert issue.translation_key == "check_failing_entity"
    assert issue.translation_placeholders["entity"] == ENTITY
    assert issue.translation_placeholders["integration"] == "Example Lights"


async def test_entity_issue_not_fixable_without_registry_entry(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    await post(_entity_payload(payload, ENTITY_DETAIL))
    assert _issue(hass, ENTITY_CHECK).is_fixable is False


async def test_entity_issue_not_fixable_without_config_entry(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    er.async_get(hass).async_get_or_create(
        "sensor", "template", "uid2", suggested_object_id="example_temperature"
    )
    await post(_entity_payload(payload, ENTITY_DETAIL))
    assert _issue(hass, ENTITY_CHECK).is_fixable is False


async def test_entity_issue_not_fixable_when_not_unavailable(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    _owner(hass)
    await post(_entity_payload(payload, f"{ENTITY} does not exist"))
    assert _issue(hass, ENTITY_CHECK).is_fixable is False


async def test_reload_fix_flow(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    _owner(hass)
    await post(_entity_payload(payload, ENTITY_DETAIL))
    result = await _start_fix(hass, ENTITY_CHECK)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"]["integration"] == "Example Lights"
    manager = hass.data["repairs"]["flow_manager"]
    with patch.object(
        hass.config_entries, "async_reload", AsyncMock(return_value=True)
    ) as reload:
        result = await manager.async_configure(result["flow_id"], {})
    reload.assert_awaited_once_with("owner1")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reloaded"
    # The issue stays until the watcher reports ok.
    assert _issue(hass, ENTITY_CHECK) is not None


async def test_reload_fix_flow_failure_and_gone_entity(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    _owner(hass)
    await post(_entity_payload(payload, ENTITY_DETAIL))
    started = await _start_fix(hass, ENTITY_CHECK)
    manager = hass.data["repairs"]["flow_manager"]
    with patch.object(
        hass.config_entries, "async_reload", AsyncMock(side_effect=HomeAssistantError)
    ):
        result = await manager.async_configure(started["flow_id"], {})
    assert result["reason"] == "reload_failed"

    started = await _start_fix(hass, ENTITY_CHECK)
    er.async_get(hass).async_remove(ENTITY)
    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload:
        result = await manager.async_configure(started["flow_id"], {})
    reload.assert_not_awaited()
    assert result["reason"] == "entity_not_found"


async def test_backup_issue_not_fixable_without_service(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    await post(payload)
    assert _issue(hass, "backup").is_fixable is False


async def test_backup_fix_flow_calls_service(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    calls = async_mock_service(hass, "backup", "create_automatic")
    await post(payload)
    issue = _issue(hass, "backup")
    assert issue.is_fixable is True
    assert issue.translation_key == "check_failing_backup"
    result = await _start_fix(hass, "backup")
    assert result["type"] is FlowResultType.FORM
    assert not calls  # nothing happens before the user confirms
    manager = hass.data["repairs"]["flow_manager"]
    result = await manager.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "backup_started"
    assert _issue(hass, "backup") is not None


async def test_backup_fix_flow_service_vanished(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    async_mock_service(hass, "backup", "create_automatic")
    await post(payload)
    started = await _start_fix(hass, "backup")
    hass.services.async_remove("backup", "create_automatic")
    manager = hass.data["repairs"]["flow_manager"]
    result = await manager.async_configure(started["flow_id"], {})
    assert result["reason"] == "backup_unavailable"
