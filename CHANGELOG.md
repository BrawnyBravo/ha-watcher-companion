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
