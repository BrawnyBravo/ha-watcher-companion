"""Runtime state for one HA Watcher config entry.

The hub owns the webhook, the last valid payload, the online/offline state and
the offline timer. Entities read from it and subscribe for updates.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import copy
from datetime import datetime, timedelta
import hashlib
import hmac
from http import HTTPStatus
import json
import logging
import re
import time
from typing import Any

from aiohttp import web
import voluptuous as vol

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CHECK_NAME_PATTERN,
    CONF_LOCAL_ONLY,
    CONF_OFFLINE_TIMEOUT,
    CONF_SHARED_SECRET,
    CONF_WEBHOOK_ID,
    DEFAULT_LOCAL_ONLY,
    DEFAULT_OFFLINE_TIMEOUT,
    DOMAIN,
    EVENT_OFFLINE,
    EVENT_ONLINE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    MAX_BODY_BYTES,
    MAX_CLOCK_AHEAD,
    MAX_CLOCK_SKEW,
    MAX_KNOWN_CHECKS,
    SIGNATURE_PREFIX,
    STORAGE_SAVE_DELAY,
    STORAGE_VERSION,
)
from .payload import error_location, validate_payload

_LOGGER = logging.getLogger(__name__)

_CHECK_NAME_RE = re.compile(CHECK_NAME_PATTERN)
# A named tuple of exceptions keeps the except clause valid before Python 3.14
# (the unparenthesised PEP 758 form is not) and stable under the formatter.
_JSON_ERRORS = (ValueError, RecursionError)
_RESTORE_ERRORS = (vol.Invalid, TypeError, ValueError, RecursionError)

type HAWatcherConfigEntry = ConfigEntry[WatcherHub]


def webhook_url(hass: HomeAssistant, webhook_id: str) -> str:
    """Best URL for the watcher to post to; prefers the local address."""
    try:
        return webhook.async_generate_url(
            hass, webhook_id, allow_ip=True, prefer_external=False
        )
    except NoURLAvailableError:
        path = webhook.async_generate_path(webhook_id)
        return f"http://<your-home-assistant-address>:8123{path}"


def check_unique_id(entry_id: str, name: str) -> str:
    """Unique id of the entity for one check."""
    return f"{entry_id}_check_{name}"


@callback
def _is_remove_event(data: Mapping[str, Any]) -> bool:
    return bool(data["action"] == "remove")


def _restored_payload(value: Any) -> dict[str, Any] | None:
    """The stored last report, or None if it no longer passes validation."""
    if value is None:
        return None
    try:
        return validate_payload(copy.deepcopy(value))
    except _RESTORE_ERRORS:
        _LOGGER.warning("Ignored a stored status report that is no longer valid")
        return None


def _restored_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt_util.parse_datetime(value)
    except ValueError:
        return None
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed


class _Rejected(Exception):
    """A report refused with a short reason (never payload values)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Unauthorized(_Rejected):
    """A report without a valid signature while a shared secret is set."""


class WatcherHub:
    """Holds the latest report from one watcher and tracks whether it is online."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.webhook_id: str = entry.data[CONF_WEBHOOK_ID]
        options = entry.options
        self.local_only: bool = options.get(CONF_LOCAL_ONLY, DEFAULT_LOCAL_ONLY)
        self.timeout = timedelta(
            minutes=options.get(CONF_OFFLINE_TIMEOUT, DEFAULT_OFFLINE_TIMEOUT)
        )
        self.shared_secret: str | None = options.get(CONF_SHARED_SECRET) or None
        self.payload: dict[str, Any] | None = None
        self.last_report: datetime | None = None
        # None = unknown (just started, no push yet and timeout not reached).
        # True only after a fresh report in this session.
        self.online: bool | None = None
        # Last known online state, also from before a restart; decides whether
        # a transition is worth an event.
        self._previous_known: bool | None = None
        self.bad_payloads = 0
        self.unauthorized = 0
        self._bad_logged = False
        self._auth_logged = False
        self._process_logged = False
        self._listener_logged = False
        self._interval_warned = False
        self._invalid_names_logged = False
        self._cap_logged = False
        # Every check name that has (or had) an entity, in order. Persisted and
        # capped at MAX_KNOWN_CHECKS so a sender cannot flood the registry.
        self.known_checks: list[str] = []
        # Names that have a live entity object in this session.
        self._entity_checks: set[str] = set()
        # Timestamp of the last accepted signed request; signed requests must
        # be strictly newer, which blocks replays (also across restarts).
        self.last_signed_ts: int | None = None
        self._timer: CALLBACK_TYPE | None = None
        # Set once the hub is shut down; a report already in flight may still
        # be applied, but it no longer arms timers or creates entities.
        self._stopped = False
        self._save_timer: CALLBACK_TYPE | None = None
        self._unsub_registry: CALLBACK_TYPE | None = None
        self._unsub_stop: CALLBACK_TYPE | None = None
        self._listeners: list[Callable[[], None]] = []
        self._new_check_listeners: list[Callable[[list[str]], None]] = []
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )

    # ---- lifecycle -------------------------------------------------------

    async def async_setup(self) -> None:
        """Restore stored state, register the webhook and start the timer."""
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            # The file may be damaged or written by another release; each part
            # is checked and dropped on its own rather than failing setup.
            self.payload = _restored_payload(stored.get("payload"))
            self.last_report = _restored_datetime(stored.get("last_report"))
            online = stored.get("online")
            self._previous_known = online if isinstance(online, bool) else None
            last_ts = stored.get("last_signed_ts")
            if isinstance(last_ts, int) and not isinstance(last_ts, bool):
                self.last_signed_ts = last_ts
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
        self._unsub_registry = self.hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED,
            self._async_registry_updated,
            event_filter=_is_remove_event,
        )
        # Home Assistant does not unload config entries when it stops, so the
        # pending routine save would be lost without this.
        self._unsub_stop = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._async_handle_stop
        )
        # Give the watcher one full timeout after startup before calling it offline.
        self._schedule_timeout()

    @callback
    def _shutdown(self) -> None:
        """Release the webhook, timers and listeners (safe to call twice)."""
        self._stopped = True
        webhook.async_unregister(self.hass, self.webhook_id)
        # Platforms are torn down after this; no new check entity may be
        # added to a platform that is going away.
        self._new_check_listeners.clear()
        self._cancel_timer()
        if self._save_timer is not None:
            self._save_timer()
            self._save_timer = None
        if self._unsub_registry is not None:
            self._unsub_registry()
            self._unsub_registry = None
        if self._unsub_stop is not None:
            self._unsub_stop()
            self._unsub_stop = None

    async def _async_handle_stop(self, _event: Event) -> None:
        """Write the final state when Home Assistant stops."""
        self._unsub_stop = None  # a one-time listener is gone once it has run
        await self.async_unload()

    async def async_unload(self) -> None:
        """Release everything and write the final state."""
        self._shutdown()
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
        """Call `add` with check names that need a new entity."""
        self._new_check_listeners.append(add)

        @callback
        def remove() -> None:
            # Already gone if the hub was shut down first (see _shutdown).
            if add in self._new_check_listeners:
                self._new_check_listeners.remove(add)

        return remove

    @callback
    def _notify(self) -> None:
        # One failing entity must not stop the others from updating.
        for update in list(self._listeners):
            try:
                update()
            except Exception as err:
                if not self._listener_logged:
                    self._listener_logged = True
                    _LOGGER.warning(
                        "An entity of %s could not update (%s)",
                        self.entry.title,
                        type(err).__name__,
                    )

    # ---- data access -----------------------------------------------------

    def check(self, name: str) -> dict[str, Any] | None:
        if not self.payload:
            return None
        result: dict[str, Any] | None = self.payload["checks"].get(name)
        return result

    @callback
    def async_initial_checks(self) -> list[str]:
        """Checks that get an entity at startup: known and in the last report.

        Known checks missing from the last report get no entity, so the user can
        delete their leftover registry entries (which frees their slot).
        """
        reported = self.payload["checks"] if self.payload else {}
        names = [name for name in self.known_checks if name in reported]
        self._entity_checks.update(names)
        return names

    @callback
    def _async_registry_updated(
        self, event: Event[er.EventEntityRegistryUpdatedData]
    ) -> None:
        """Forget check names whose entity the user deleted."""
        registry = er.async_get(self.hass)
        present = {
            e.unique_id
            for e in er.async_entries_for_config_entry(registry, self.entry.entry_id)
        }
        entry_id = self.entry.entry_id
        gone = [
            name
            for name in self.known_checks
            if check_unique_id(entry_id, name) not in present
        ]
        if not gone:
            return
        for name in gone:
            self.known_checks.remove(name)
            self._entity_checks.discard(name)
        self._cap_logged = False
        self._save_now()

    # ---- webhook ---------------------------------------------------------

    async def _handle_webhook(
        self, hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        # Nothing raised here may reach Home Assistant's webhook logger, which
        # logs the webhook id; every failure is answered and logged here.
        try:
            payload = await self._read_payload(request)
        except _Unauthorized as err:
            return self._unauthorized(err.reason)
        except _Rejected as err:
            return self._reject(err.reason)
        except Exception as err:
            return self._reject(f"unexpected {type(err).__name__}")
        try:
            self.async_process_payload(payload)
        except Exception as err:
            if not self._process_logged:
                self._process_logged = True
                _LOGGER.error(
                    "Could not apply a status report for %s (%s)",
                    self.entry.title,
                    type(err).__name__,
                )
            return web.json_response(
                {"ok": False, "error": "processing failed"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        # A good (and, with a secret, authenticated) report re-arms the
        # one-shot warnings so a later problem is logged again.
        self._auth_logged = False
        self._process_logged = False
        return web.json_response({"ok": True})

    async def _read_payload(self, request: web.Request) -> dict[str, Any]:
        """Read, size-limit, authenticate, parse and validate a request body."""
        too_big = f"body larger than {MAX_BODY_BYTES} bytes"
        if (request.content_length or 0) > MAX_BODY_BYTES:
            raise _Rejected(too_big)
        body = bytearray()
        while chunk := await request.content.read(MAX_BODY_BYTES):
            body += chunk
            if len(body) > MAX_BODY_BYTES:
                raise _Rejected(too_big)
        if self.shared_secret:
            self._verify_signature(request, bytes(body))
        try:
            data = json.loads(body)
        except _JSON_ERRORS:
            raise _Rejected("body is not valid JSON") from None
        try:
            return validate_payload(data)
        except vol.Invalid as err:
            raise _Rejected(error_location(err)) from None

    def _verify_signature(self, request: web.Request, body: bytes) -> None:
        """Check the HMAC signature headers; raise _Unauthorized if wrong."""
        assert self.shared_secret is not None
        stamp = request.headers.get(HEADER_TIMESTAMP)
        signature = request.headers.get(HEADER_SIGNATURE)
        if not stamp or not signature:
            raise _Unauthorized("signature headers missing")
        if not stamp.isascii() or not stamp.isdigit() or len(stamp) > 12:
            raise _Unauthorized("timestamp header is not a number")
        lag = time.time() - int(stamp)
        if lag > MAX_CLOCK_SKEW or -lag > MAX_CLOCK_AHEAD:
            raise _Unauthorized(
                f"timestamp more than {MAX_CLOCK_SKEW} s behind or "
                f"{MAX_CLOCK_AHEAD} s ahead of this clock"
            )
        if not signature.startswith(SIGNATURE_PREFIX):
            raise _Unauthorized("signature header has the wrong format")
        digest = hmac.new(
            self.shared_secret.encode(), stamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        expected = f"{SIGNATURE_PREFIX}{digest}".encode()
        if not hmac.compare_digest(expected, signature.encode("utf-8", "replace")):
            raise _Unauthorized("signature does not match")
        if self.last_signed_ts is not None and int(stamp) <= self.last_signed_ts:
            raise _Unauthorized("timestamp not newer than the last accepted report")
        self.last_signed_ts = int(stamp)
        # Saved with the routine save; a crash inside that window could allow
        # one replay of a request newer than the saved value (documented).
        self._save_soon()

    def _unauthorized(self, reason: str) -> web.Response:
        self.unauthorized += 1
        if not self._auth_logged:
            self._auth_logged = True
            _LOGGER.warning(
                "Rejected an unauthenticated status report for %s: %s "
                "(further ones are not logged)",
                self.entry.title,
                reason,
            )
        return web.json_response(
            {"ok": False, "error": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED
        )

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
        self._schedule_timeout()
        urgent = self._set_online(True)
        if grown := [n for n in payload["checks"] if n not in self.known_checks]:
            self.known_checks.extend(grown)
            urgent = True
        if urgent:
            # Check-name growth (the flood cap) and online changes must survive
            # a crash or power cut, so they are written straight away.
            self._save_now()
        else:
            self._save_soon()
        if new := [n for n in payload["checks"] if n not in self._entity_checks]:
            self._entity_checks.update(new)
            for add in list(self._new_check_listeners):
                add(new)
        self._warn_interval(payload["interval_s"])
        if payload["version"] != old_version:
            self._update_device_version(payload["version"])
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
                "Delete check entities the watcher no longer reports to free slots",
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
    def _set_online(self, online: bool) -> bool:
        """Set the state; returns True when the stored state changed."""
        previous = self.online if self.online is not None else self._previous_known
        changed = self._previous_known is not online
        self.online = online
        self._previous_known = online
        if online and previous is False:
            # Recovered (also across a restart if it was offline before).
            self._fire(EVENT_ONLINE)
        elif not online and previous is not False:
            # Went quiet, or never reported within the timeout after startup.
            self._fire(EVENT_OFFLINE)
        return changed

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
        if self._stopped:
            return
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
        if self._set_online(False):
            self._save_now()
        self._notify()

    # ---- storage ---------------------------------------------------------

    @callback
    def _save_now(self) -> None:
        """Write the current state now (any pending routine save is folded in)."""
        if self._save_timer is not None:
            self._save_timer()
            self._save_timer = None
        self.hass.async_create_task(
            self._store.async_save(self._data_to_store()),
            f"{DOMAIN} save {self.entry.entry_id}",
            eager_start=True,
        )

    @callback
    def _save_soon(self) -> None:
        """Write within STORAGE_SAVE_DELAY of the first unsaved change.

        Later changes do not push the write back, so steady reports cannot
        starve it (Store.async_delay_save would).
        """
        if self._save_timer is None and not self._stopped:
            # After shutdown the final save in async_unload writes everything.
            self._save_timer = async_call_later(
                self.hass, STORAGE_SAVE_DELAY, self._handle_save_timer
            )

    @callback
    def _handle_save_timer(self, _now: datetime) -> None:
        self._save_timer = None
        self._save_now()

    def _data_to_store(self) -> dict[str, Any]:
        return {
            "payload": self.payload,
            "last_report": self.last_report.isoformat() if self.last_report else None,
            "online": self._previous_known,
            "known_checks": list(self.known_checks),
            "last_signed_ts": self.last_signed_ts,
        }

    # ---- diagnostics -----------------------------------------------------

    def diagnostics(self) -> dict[str, Any]:
        return {
            "webhook_path": webhook.async_generate_path(self.webhook_id),
            "local_only": self.local_only,
            "signing_enabled": self.shared_secret is not None,
            "offline_timeout_minutes": int(self.timeout.total_seconds() // 60),
            "online": self.online,
            "last_report": self.last_report.isoformat() if self.last_report else None,
            "rejected_payloads_since_start": self.bad_payloads,
            "unauthorized_since_start": self.unauthorized,
            "known_checks": list(self.known_checks),
            "payload": self.payload,
        }
