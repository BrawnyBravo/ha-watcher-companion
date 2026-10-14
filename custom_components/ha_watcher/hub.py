"""Runtime state for one HA Watcher config entry.

The hub owns the webhook, the last valid payload, the online/offline state and
the offline timer. Entities read from it and subscribe for updates.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from http import HTTPStatus
import json
import logging
import re
from typing import Any

from aiohttp import web
import voluptuous as vol

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CHECK_NAME_PATTERN,
    CONF_LOCAL_ONLY,
    CONF_OFFLINE_TIMEOUT,
    CONF_WEBHOOK_ID,
    DEFAULT_LOCAL_ONLY,
    DEFAULT_OFFLINE_TIMEOUT,
    DOMAIN,
    EVENT_OFFLINE,
    EVENT_ONLINE,
    MAX_BODY_BYTES,
    MAX_KNOWN_CHECKS,
    STORAGE_SAVE_DELAY,
    STORAGE_VERSION,
)
from .payload import error_location, validate_payload

_LOGGER = logging.getLogger(__name__)

_CHECK_NAME_RE = re.compile(CHECK_NAME_PATTERN)

type HAWatcherConfigEntry = ConfigEntry[WatcherHub]


def webhook_url(hass: HomeAssistant, webhook_id: str) -> str:
    """Best URL for the watcher to post to; prefers the local address."""
    try:
        return webhook.async_generate_url(
            hass, webhook_id, allow_ip=True, prefer_external=False
        )
    except NoURLAvailableError:
        return f"http://<your-home-assistant-address>:8123{webhook.async_generate_path(webhook_id)}"


class WatcherHub:
    """Holds the latest report from one watcher and tracks whether it is online."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.webhook_id: str = entry.data[CONF_WEBHOOK_ID]
        self.local_only: bool = entry.options.get(CONF_LOCAL_ONLY, DEFAULT_LOCAL_ONLY)
        self.timeout = timedelta(
            minutes=entry.options.get(CONF_OFFLINE_TIMEOUT, DEFAULT_OFFLINE_TIMEOUT)
        )
        self.payload: dict[str, Any] | None = None
        self.last_report: datetime | None = None
        # None = unknown (just started, no push yet and timeout not reached).
        self.online: bool | None = None
        # Last known online state from before a restart, used only to decide
        # whether a transition is worth an event.
        self._previous_known: bool | None = None
        self.bad_payloads = 0
        self._bad_logged = False
        self._interval_warned = False
        self._invalid_names_logged = False
        self._cap_logged = False
        # Every check name that ever got an entity, in order. Persisted, and
        # capped at MAX_KNOWN_CHECKS so a sender cannot flood the registry.
        self.known_checks: list[str] = []
        self._timer: CALLBACK_TYPE | None = None
        self._listeners: list[Callable[[], None]] = []
        self._new_check_listeners: list[Callable[[list[str]], None]] = []
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )

    # ---- lifecycle -------------------------------------------------------

    async def async_setup(self) -> None:
        """Restore stored state, register the webhook and start the timer."""
        stored = await self._store.async_load()
        if stored:
            self.payload = stored.get("payload")
            if (raw := stored.get("last_report")) is not None:
                self.last_report = dt_util.parse_datetime(raw)
            self._previous_known = stored.get("online")
            self.known_checks = [
                name
                for name in stored.get("known_checks") or []
                if isinstance(name, str) and _CHECK_NAME_RE.fullmatch(name)
            ][:MAX_KNOWN_CHECKS]
        webhook.async_register(
            self.hass,
            DOMAIN,
            self.entry.title,
            self.webhook_id,
            self._handle_webhook,
            local_only=self.local_only,
            allowed_methods=["POST"],
        )
        # Give the watcher one full timeout after startup before calling it offline.
        self._schedule_timeout()

    async def async_unload(self) -> None:
        """Unregister the webhook, stop the timer and save state."""
        webhook.async_unregister(self.hass, self.webhook_id)
        self._cancel_timer()
        await self._store.async_save(self._data_to_store())

    async def async_remove(self) -> None:
        """Delete stored state when the entry is removed."""
        await self._store.async_remove()

    # ---- subscriptions ---------------------------------------------------

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> CALLBACK_TYPE:
        """Call `update` whenever the hub state changes."""
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    @callback
    def async_add_check_listener(
        self, add: Callable[[list[str]], None]
    ) -> CALLBACK_TYPE:
        """Call `add` with names of checks seen for the first time."""
        self._new_check_listeners.append(add)
        return lambda: self._new_check_listeners.remove(add)

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    # ---- data access -----------------------------------------------------

    def check(self, name: str) -> dict[str, Any] | None:
        if not self.payload:
            return None
        result: dict[str, Any] | None = self.payload["checks"].get(name)
        return result

    # ---- webhook ---------------------------------------------------------

    async def _handle_webhook(
        self, hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        # Nothing raised here may reach Home Assistant's webhook logger, which
        # would log the request; every failure goes through _reject instead.
        try:
            payload = await self._read_payload(request)
        except _Rejected as err:
            return self._reject(err.reason)
        except Exception as err:
            return self._reject(f"unexpected {type(err).__name__}")
        self.async_process_payload(payload)
        return web.json_response({"ok": True})

    async def _read_payload(self, request: web.Request) -> dict[str, Any]:
        """Read, size-limit, parse and validate a request body."""
        too_big = f"body larger than {MAX_BODY_BYTES} bytes"
        if (request.content_length or 0) > MAX_BODY_BYTES:
            raise _Rejected(too_big)
        body = bytearray()
        while chunk := await request.content.read(MAX_BODY_BYTES):
            body += chunk
            if len(body) > MAX_BODY_BYTES:
                raise _Rejected(too_big)
        try:
            data = json.loads(body)
        except (ValueError, RecursionError):
            raise _Rejected("body is not valid JSON") from None
        try:
            return validate_payload(data)
        except vol.Invalid as err:
            raise _Rejected(error_location(err)) from None

    def _reject(self, reason: str) -> web.Response:
        self.bad_payloads += 1
        if not self._bad_logged:
            # Logged once until a good payload arrives; never logs payload values.
            _LOGGER.warning(
                "Rejected a status report for %s: %s", self.entry.title, reason
            )
            self._bad_logged = True
        return web.json_response(
            {"ok": False, "error": "invalid payload"}, status=HTTPStatus.BAD_REQUEST
        )

    @callback
    def async_process_payload(self, payload: dict[str, Any]) -> None:
        """Apply a validated payload."""
        if self._bad_logged:
            _LOGGER.info("Status reports for %s are valid again", self.entry.title)
        self._bad_logged = False
        payload["checks"] = self._filter_checks(payload["checks"])
        old_version = self.payload["version"] if self.payload else None
        self.payload = payload
        self.last_report = dt_util.utcnow()
        self._warn_interval(payload["interval_s"])
        if payload["version"] != old_version:
            self._update_device_version(payload["version"])
        if new := [n for n in payload["checks"] if n not in self.known_checks]:
            self.known_checks.extend(new)
            # Save the grown set soon so a crash cannot reset the cap.
            self._store.async_delay_save(self._data_to_store, 1)
            for add in list(self._new_check_listeners):
                add(new)
        self._set_online(True)
        self._schedule_timeout()
        self._store.async_delay_save(self._data_to_store, STORAGE_SAVE_DELAY)
        self._notify()

    def _filter_checks(
        self, checks: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Drop invalid names and new names past the lifetime cap (logged once)."""
        invalid = [n for n in checks if not _CHECK_NAME_RE.fullmatch(n)]
        if invalid and not self._invalid_names_logged:
            self._invalid_names_logged = True
            _LOGGER.warning(
                "Ignored %s check(s) with invalid names from %s; check names must "
                "be 1-40 characters of a-z, 0-9 and _",
                len(invalid),
                self.entry.title,
            )
        room = MAX_KNOWN_CHECKS - len(self.known_checks)
        kept: dict[str, dict[str, Any]] = {}
        refused = 0
        for name, check in checks.items():
            if name in invalid:
                continue
            if name not in self.known_checks:
                if room <= 0:
                    refused += 1
                    continue
                room -= 1
            kept[name] = check
        if refused and not self._cap_logged:
            self._cap_logged = True
            _LOGGER.warning(
                "%s: check limit of %s reached; ignoring %s new check name(s). "
                "Remove this integration entry and add it again to reset the limit",
                self.entry.title,
                MAX_KNOWN_CHECKS,
                refused,
            )
        return kept

    def _warn_interval(self, interval_s: int) -> None:
        if self._interval_warned or interval_s * 2 <= self.timeout.total_seconds():
            return
        self._interval_warned = True
        _LOGGER.warning(
            "%s reports every %s seconds but the offline timeout is %s minutes; "
            "raise the timeout to at least twice the report interval to avoid "
            "false offline alerts",
            self.entry.title,
            interval_s,
            int(self.timeout.total_seconds() // 60),
        )

    def _update_device_version(self, version: str) -> None:
        registry = dr.async_get(self.hass)
        identifier = (DOMAIN, self.entry.entry_id)
        if hasattr(registry, "async_get_device_by_identifier"):
            device = registry.async_get_device_by_identifier(
                identifier, self.entry.entry_id
            )
        else:  # pragma: no cover - Home Assistant before the lookup was added
            device = registry.async_get_device(identifiers={identifier})
        if device is not None:
            registry.async_update_device(device.id, sw_version=version)

    # ---- online / offline ------------------------------------------------

    @callback
    def _set_online(self, online: bool) -> None:
        previous = self.online if self.online is not None else self._previous_known
        self.online = online
        self._previous_known = online
        if online and previous is False:
            # Recovered (also across a restart if it was offline before).
            self._fire(EVENT_ONLINE)
        elif not online and previous is not False:
            # Went quiet, or never reported within the timeout after startup.
            self._fire(EVENT_OFFLINE)

    def _fire(self, event_type: str) -> None:
        self.hass.bus.async_fire(
            event_type,
            {
                "entry_id": self.entry.entry_id,
                "name": self.entry.title,
                "last_report": self.last_report.isoformat()
                if self.last_report
                else None,
            },
        )

    def _schedule_timeout(self) -> None:
        self._cancel_timer()
        self._timer = async_call_later(
            self.hass, self.timeout.total_seconds(), self._handle_timeout
        )

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer()
            self._timer = None

    @callback
    def _handle_timeout(self, _now: datetime) -> None:
        self._timer = None
        if self.online is False:
            return
        _LOGGER.info(
            "No status report from %s for %s minutes; marking it offline",
            self.entry.title,
            int(self.timeout.total_seconds() // 60),
        )
        self._set_online(False)
        self._store.async_delay_save(self._data_to_store, STORAGE_SAVE_DELAY)
        self._notify()

    # ---- storage / diagnostics -------------------------------------------

    def _data_to_store(self) -> dict[str, Any]:
        return {
            "payload": self.payload,
            "last_report": self.last_report.isoformat() if self.last_report else None,
            "online": self._previous_known,
            "known_checks": self.known_checks,
        }

    def diagnostics(self) -> dict[str, Any]:
        return {
            "webhook_url": webhook_url(self.hass, self.webhook_id),
            "local_only": self.local_only,
            "offline_timeout_minutes": int(self.timeout.total_seconds() // 60),
            "online": self.online,
            "last_report": self.last_report.isoformat() if self.last_report else None,
            "rejected_payloads_since_start": self.bad_payloads,
            "payload": self.payload,
        }


class _Rejected(Exception):
    """A report that is refused with a short reason (no payload values)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
