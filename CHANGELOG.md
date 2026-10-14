# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-14

### Added

- First release.
- UI setup that creates a webhook for one ha-watcher service and shows its address.
- Options for the offline timeout (default 5 minutes, minimum 2) and local-only webhook.
- Entities: Watcher online, Home Assistant outage, one problem sensor per watcher check
  (added automatically as new checks appear), Last report, Alerts sent (24h),
  Last power cycle and Last recovery command.
- `ha_watcher_offline` and `ha_watcher_online` events.
- Diagnostics with the webhook id redacted.
- Status payload contract v1, documented in `docs/PAYLOAD.md`.
- Safety limits on the webhook: 64 KiB body, 32 checks per report, check names must
  match `[a-z0-9_]{1,40}`, at most 32 check entities per entry over its lifetime
  (persisted). Malformed input of any kind (deep JSON nesting, out-of-range dates)
  gets a 400 and never reaches Home Assistant's error log.
- Integer ranges in the payload (for example `interval_s` 1-86400); errors while applying
  a valid report are contained (500, short log line).
- Optional shared-secret signing (`X-HA-Watcher-Timestamp`, `X-HA-Watcher-Signature`
  HMAC-SHA256, 300 s window, strictly increasing timestamps that survive restarts;
  password field with a suggested random secret, minimum 16 characters).
- State is saved promptly (check names and online changes at once, the rest within 5 minutes),
  so it survives a crash or power cut.
- Deleting a retired check entity frees its slot in the 32-check limit.
- After a restart, restored check data stays unavailable until the first fresh report.
- Diagnostics also hide the address, entry name, shared secret and check details.
