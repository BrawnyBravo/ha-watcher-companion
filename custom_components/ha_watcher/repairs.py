"""Repair issues for failing watcher checks, and the fixes Home Assistant can do.

One issue per failing check. The issue is created when a report says the check
is failing, updated in place (same id, so the user's "Ignore" survives) while
it keeps failing, and deleted when the check reports ok again or is no longer
reported. Issues are not persistent: they describe pushed data, so after a
restart they come back with the next report instead of showing a state that
may have changed while Home Assistant was down.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, issue_registry as ir

from .const import CONF_CREATE_REPAIRS, DEFAULT_CREATE_REPAIRS, DOMAIN

LEARN_MORE_URL = "https://github.com/BrawnyBravo/ha-watcher-companion#repairs"

# Checks that mean Home Assistant itself (or its host) is in trouble.
ERROR_CHECKS = frozenset({"api", "core", "host"})

KIND_RELOAD = "reload_entity_owner"
KIND_BACKUP = "start_backup"

BACKUP_CHECK = "backup"
BACKUP_DOMAIN = "backup"
BACKUP_SERVICE = "create_automatic"

# The watcher names the check for entity `sensor.x` "entity_sensor_x"
# (docs/PAYLOAD.md) and starts its detail text with the entity id.
_ENTITY_DETAIL = re.compile(
    r"(?:entity:\S+: )?([a-z0-9_]+\.[a-z0-9_]+) has been (?:unavailable|unknown) "
)
_NOT_NAME = re.compile(r"[^a-z0-9_]")
_NAME_LEN = 40


def issue_prefix(entry_id: str) -> str:
    """Start of the id of every check issue of one config entry."""
    return f"check_{entry_id}_"


def _contract_name(entity_id: str) -> str:
    """The check name the watcher sends for `entity:<entity_id>` (PAYLOAD.md)."""
    key = f"entity:{entity_id}"
    name = _NOT_NAME.sub("_", key.lower())
    if len(name) > _NAME_LEN:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
        name = f"{name[:31]}_{digest}"
    return name


def entity_id_of(name: str, detail: str) -> str | None:
    """The entity id behind an "entity unavailable" check, or None.

    The id is read from the detail text and accepted only when it maps to the
    check name by the contract's naming rule, so a stray detail text cannot
    point the fix at another entity.
    """
    if not name.startswith("entity_"):
        return None
    match = _ENTITY_DETAIL.match(detail)
    if match is None or _contract_name(match[1]) != name:
        return None
    return match[1]


def _owner_entry(
    hass: HomeAssistant, entity_id: str, own_entry_id: str
) -> ConfigEntry | None:
    """The config entry that owns the entity, if there is a reloadable one."""
    registry_entry = er.async_get(hass).async_get(entity_id)
    if registry_entry is None or registry_entry.config_entry_id is None:
        return None
    if registry_entry.config_entry_id == own_entry_id:
        return None
    return hass.config_entries.async_get_entry(registry_entry.config_entry_id)


class CheckIssues:
    """Keeps the repair issues of one config entry in step with its checks."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

    @property
    def enabled(self) -> bool:
        return bool(self.entry.options.get(CONF_CREATE_REPAIRS, DEFAULT_CREATE_REPAIRS))

    def _existing(self) -> set[str]:
        prefix = issue_prefix(self.entry.entry_id)
        return {
            issue_id
            for domain, issue_id in ir.async_get(self.hass).issues
            if domain == DOMAIN and issue_id.startswith(prefix)
        }

    @callback
    def async_update(self, checks: dict[str, dict[str, Any]]) -> None:
        """Apply the checks of a new report."""
        if not self.enabled:
            self.async_clear()
            return
        prefix = issue_prefix(self.entry.entry_id)
        keep: set[str] = set()
        for name, check in checks.items():
            issue_id = f"{prefix}{name}"
            if check["ok"] is False:
                keep.add(issue_id)
                self._create(issue_id, name, check["detail"])
            elif check["ok"] is None:
                # Not run yet: unknown, so leave an existing issue as it is.
                keep.add(issue_id)
        for issue_id in self._existing() - keep:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    @callback
    def async_clear(self) -> None:
        """Remove every check issue of this entry."""
        for issue_id in self._existing():
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _create(self, issue_id: str, name: str, detail: str) -> None:
        placeholders = {
            "check": name,
            "message": detail,
            "watcher": self.entry.title,
        }
        data: dict[str, str | int | float | None] = {"entry_id": self.entry.entry_id}
        translation_key = "check_failing"
        fixable = False
        entity_id = entity_id_of(name, detail)
        owner = (
            _owner_entry(self.hass, entity_id, self.entry.entry_id)
            if entity_id
            else None
        )
        if entity_id and owner is not None:
            fixable = True
            translation_key = "check_failing_entity"
            data |= {"kind": KIND_RELOAD, "entity_id": entity_id}
            placeholders |= {"entity": entity_id, "integration": owner.title}
        elif name == BACKUP_CHECK and self.hass.services.has_service(
            BACKUP_DOMAIN, BACKUP_SERVICE
        ):
            fixable = True
            translation_key = "check_failing_backup"
            data["kind"] = KIND_BACKUP
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            data=data,
            is_fixable=fixable,
            is_persistent=False,
            learn_more_url=LEARN_MORE_URL,
            severity=(
                ir.IssueSeverity.ERROR
                if name in ERROR_CHECKS
                else ir.IssueSeverity.WARNING
            ),
            translation_key=translation_key,
            translation_placeholders=placeholders,
        )


class ReloadEntityOwnerFlow(ConfirmRepairFlow):
    """Reload the integration that owns an unavailable entity."""

    def __init__(self, entity_id: str, own_entry_id: str) -> None:
        self._entity_id = entity_id
        self._own_entry_id = own_entry_id

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> Any:
        """Ask first, then reload."""
        if user_input is None:
            return await super().async_step_confirm()
        owner = _owner_entry(self.hass, self._entity_id, self._own_entry_id)
        if owner is None:
            return self.async_abort(reason="entity_not_found")
        try:
            await self.hass.config_entries.async_reload(owner.entry_id)
        except HomeAssistantError:
            return self.async_abort(reason="reload_failed")
        # Not async_create_entry: that would delete the issue now, but only
        # the watcher's next "ok" report may clear it.
        return self.async_abort(reason="reloaded")


class StartBackupFlow(ConfirmRepairFlow):
    """Ask Home Assistant's backup integration to make a backup."""

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> Any:
        """Ask first, then start the backup."""
        if user_input is None:
            return await super().async_step_confirm()
        if not self.hass.services.has_service(BACKUP_DOMAIN, BACKUP_SERVICE):
            return self.async_abort(reason="backup_unavailable")
        try:
            # Not waited for: a backup can take minutes.
            await self.hass.services.async_call(
                BACKUP_DOMAIN, BACKUP_SERVICE, {}, blocking=False
            )
        except HomeAssistantError:
            return self.async_abort(reason="backup_failed")
        return self.async_abort(reason="backup_started")


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for a fixable check issue."""
    data = data or {}
    if data.get("kind") == KIND_RELOAD:
        return ReloadEntityOwnerFlow(
            str(data.get("entity_id")), str(data.get("entry_id"))
        )
    return StartBackupFlow()
