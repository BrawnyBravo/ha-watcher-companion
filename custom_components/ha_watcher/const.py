"""Constants for the HA Watcher integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ha_watcher"

CONF_WEBHOOK_ID: Final = "webhook_id"
CONF_LOCAL_ONLY: Final = "local_only"
CONF_OFFLINE_TIMEOUT: Final = "offline_timeout"
CONF_SHARED_SECRET: Final = "shared_secret"

DEFAULT_NAME: Final = "HA Watcher"
DEFAULT_LOCAL_ONLY: Final = True
DEFAULT_OFFLINE_TIMEOUT: Final = 5  # minutes
MIN_OFFLINE_TIMEOUT: Final = 2  # minutes
MAX_OFFLINE_TIMEOUT: Final = 1440  # minutes (one day)

EVENT_OFFLINE: Final = "ha_watcher_offline"
EVENT_ONLINE: Final = "ha_watcher_online"

# Highest payload contract version this release was written against.
# Newer versions are accepted as long as the v1 keys are present.
PAYLOAD_VERSION: Final = 1

# Guard rails on untrusted input.
MAX_BODY_BYTES: Final = 64 * 1024
MAX_CHECKS: Final = 32  # per payload
MAX_KNOWN_CHECKS: Final = 32  # distinct check names (entities) per entry, ever
CHECK_NAME_PATTERN: Final = r"[a-z0-9_]{1,40}"
MAX_CHECK_NAME_LEN: Final = 128  # structural limit before the pattern is applied
MAX_DETAIL_LEN: Final = 1024
MAX_TEXT_LEN: Final = 128

STORAGE_VERSION: Final = 1
# Routine saves (last report, payload) are written at most this long after the
# first change; check-name growth and online/offline changes are written at once.
STORAGE_SAVE_DELAY: Final = 300  # seconds (gentle on SD cards)

# Optional request signing (see docs/PAYLOAD.md).
HEADER_TIMESTAMP: Final = "X-HA-Watcher-Timestamp"
HEADER_SIGNATURE: Final = "X-HA-Watcher-Signature"
SIGNATURE_PREFIX: Final = "sha256="
MAX_CLOCK_SKEW: Final = 300  # seconds a signed timestamp may lag this clock
# Seconds a signed timestamp may run ahead. Kept well under the minimum offline
# timeout: an accepted future stamp becomes the replay floor, so a watcher
# clock that then steps back is refused for up to this long.
MAX_CLOCK_AHEAD: Final = 60
MIN_SECRET_LEN: Final = 16

# Integer ranges accepted in the payload.
MAX_VERSION: Final = 1000
MAX_INTERVAL_S: Final = 86_400
MAX_FAILURES: Final = 1_000_000
MAX_COUNT: Final = 1_000_000_000

MANUFACTURER: Final = "ha-watcher"
MODEL: Final = "Watcher service"
