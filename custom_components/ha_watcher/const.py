"""Constants for the HA Watcher integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ha_watcher"

CONF_WEBHOOK_ID: Final = "webhook_id"
CONF_LOCAL_ONLY: Final = "local_only"
CONF_OFFLINE_TIMEOUT: Final = "offline_timeout"

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
MAX_CHECKS: Final = 200
MAX_CHECK_NAME_LEN: Final = 128
MAX_DETAIL_LEN: Final = 1024
MAX_TEXT_LEN: Final = 128

STORAGE_VERSION: Final = 1
STORAGE_SAVE_DELAY: Final = 60  # seconds

MANUFACTURER: Final = "ha-watcher"
MODEL: Final = "Watcher service"
