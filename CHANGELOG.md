# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Setup now fills in a random shared secret and shows it with the webhook address, so
  reports are signed by default. Turning off "Only accept reports from the local
  network" (at setup or in the options) now needs a shared secret.
- Signed timestamps may now be at most 60 seconds ahead of Home Assistant's clock
  (still up to 300 seconds behind). A watcher clock that ran fast and is then
  corrected is refused for at most a minute, under the 2-minute minimum offline
  timeout.

### Added

- A repair recommends a shared secret for every entry without one (an error when
  local-only is off). Such entries keep working as before until a secret is set.
- Documentation and dialog text warn that local-only gives no protection behind a
  reverse proxy or tunnel that hides the client address.

### Fixed

- Unloading or reloading (every options save) stops taking reports before the entities
  are removed, so a late report cannot add an entity to a platform that is going away.
- A damaged or differently shaped stored state no longer stops the integration from
  loading; each invalid part is dropped on its own.
- Diagnostics replace check names (which often contain entity ids naming rooms or
  devices) with `check_1`, `check_2` and so on.
- The integration loads on Python 3.13 again (an except clause used 3.14-only syntax).
- Entities call the base class setup when added.
- `docs/PAYLOAD.md` uses the watcher's real setting names (`every_seconds`,
  `timeout_seconds`, `secret`, `watcher_id`) and says that both sides trim the secret.

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
