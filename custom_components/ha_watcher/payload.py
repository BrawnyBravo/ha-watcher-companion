"""Validation of the status payload the ha-watcher service pushes (contract v1).

See docs/PAYLOAD.md in the repository for the human-readable contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import voluptuous as vol

from homeassistant.util import dt as dt_util

from .const import (
    MAX_CHECK_NAME_LEN,
    MAX_CHECKS,
    MAX_COUNT,
    MAX_DETAIL_LEN,
    MAX_FAILURES,
    MAX_INTERVAL_S,
    MAX_TEXT_LEN,
    MAX_VERSION,
)


def _strict_int(value: Any) -> int:
    """Accept a real integer only (no bools, floats or numeric strings)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise vol.Invalid("expected an integer")
    return value


def _strict_bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise vol.Invalid("expected true or false")
    return value


def _text(max_len: int) -> Any:
    def validate(value: Any) -> str:
        if not isinstance(value, str):
            raise vol.Invalid("expected a string")
        return value[:max_len]

    return validate


def _timestamp(value: Any) -> str:
    """ISO 8601 timestamp with a timezone, normalised to UTC ISO text."""
    if not isinstance(value, str):
        raise vol.Invalid("expected an ISO 8601 timestamp string")
    try:
        parsed = dt_util.parse_datetime(value)
        if parsed is None or parsed.tzinfo is None:
            raise vol.Invalid("expected an ISO 8601 timestamp with a timezone")
        return parsed.astimezone(UTC).isoformat()
    except (ValueError, OverflowError) as err:
        # Extreme dates (year 1 with +14:00, year 9999 with -14:00) overflow.
        raise vol.Invalid("timestamp out of range") from err


def _optional_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    return _timestamp(value)


def _int_range(low: int, high: int) -> Any:
    """A real integer within [low, high]; anything else is rejected."""

    def validate(value: Any) -> int:
        value = _strict_int(value)
        if not low <= value <= high:
            raise vol.Invalid(f"must be between {low} and {high}")
        return value

    return validate


CHECK_SCHEMA = vol.Schema(
    {
        vol.Required("ok"): vol.Any(None, _strict_bool),
        vol.Required("since"): _optional_timestamp,
        vol.Required("detail"): _text(MAX_DETAIL_LEN),
        vol.Required("failures"): _int_range(0, MAX_FAILURES),
    },
    extra=vol.REMOVE_EXTRA,
)


def _checks(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise vol.Invalid("expected an object")
    if len(value) > MAX_CHECKS:
        raise vol.Invalid(f"too many checks (limit {MAX_CHECKS})")
    out: dict[str, dict[str, Any]] = {}
    for name, check in value.items():
        if not isinstance(name, str) or not name or len(name) > MAX_CHECK_NAME_LEN:
            raise vol.Invalid("check names must be 1-128 character strings")
        try:
            out[name] = CHECK_SCHEMA(check)
        except vol.Invalid as err:
            err.prepend([name])
            raise
    return out


OUTAGE_SCHEMA = vol.Schema(
    {
        vol.Required("active"): _strict_bool,
        vol.Required("since"): _optional_timestamp,
    },
    extra=vol.REMOVE_EXTRA,
)

PAYLOAD_SCHEMA = vol.Schema(
    {
        vol.Required("v"): _int_range(1, MAX_VERSION),
        vol.Required("watcher_id"): vol.All(_text(MAX_TEXT_LEN), vol.Length(min=1)),
        vol.Required("version"): _text(MAX_TEXT_LEN),
        vol.Required("sent_at"): _timestamp,
        vol.Required("interval_s"): _int_range(1, MAX_INTERVAL_S),
        vol.Required("checks"): _checks,
        vol.Required("outage"): OUTAGE_SCHEMA,
        vol.Required("alerts_sent_24h"): _int_range(0, MAX_COUNT),
        vol.Required("last_power_cycle"): _optional_timestamp,
        vol.Required("last_recovery_command"): _optional_timestamp,
    },
    extra=vol.REMOVE_EXTRA,
)


def validate_payload(data: Any) -> dict[str, Any]:
    """Validate a decoded JSON payload; raises vol.Invalid when it is unusable."""
    if not isinstance(data, dict):
        raise vol.Invalid("payload must be a JSON object")
    result: dict[str, Any] = PAYLOAD_SCHEMA(data)
    return result


def parse_ts(value: str | None) -> datetime | None:
    """Turn a validated timestamp string back into an aware datetime."""
    if value is None:
        return None
    return dt_util.parse_datetime(value)


def error_location(err: vol.Invalid) -> str:
    """Describe where a payload failed without echoing any of its values."""
    path = "".join(f"[{p!r}]" for p in err.path) or "(top level)"
    return f"{path}: {err.error_message}"
