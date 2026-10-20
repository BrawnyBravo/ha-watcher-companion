# HA Watcher for Home Assistant

[![Validate](https://github.com/BrawnyBravo/ha-watcher-companion/actions/workflows/validate.yml/badge.svg)](https://github.com/BrawnyBravo/ha-watcher-companion/actions/workflows/validate.yml)
[![Tests](https://github.com/BrawnyBravo/ha-watcher-companion/actions/workflows/tests.yml/badge.svg)](https://github.com/BrawnyBravo/ha-watcher-companion/actions/workflows/tests.yml)

Shows the status of your [ha-watcher](https://github.com/BrawnyBravo/ha-watcher) service
inside Home Assistant: whether the watcher is alive, what each of its checks says, how
many alerts it sent, and when it last power-cycled or restarted Home Assistant.

## What this is, and what it is not

**ha-watcher** is a small service that runs on **another machine** (a Raspberry Pi, a NAS,
a spare PC) and alerts you when Home Assistant goes down. It has to live outside Home
Assistant: a watchdog inside the thing it watches stops when that thing stops.

**This integration** is the Home Assistant side. The watcher pushes a short status report
to Home Assistant every minute, and this integration turns it into entities. It is a
companion, not a replacement:

- It does **not** monitor Home Assistant by itself.
- It needs the ha-watcher service installed and running on a different machine.
- When Home Assistant is down, the alerts come from the watcher, not from here.

What it adds: a dashboard view of the watcher, history of its checks, and a way to get an
alert from Home Assistant when **the watcher itself** stops reporting (who watches the
watcher?).

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=BrawnyBravo&repository=ha-watcher-companion&category=integration)

Or by hand:

1. In HACS, open the menu (three dots) and choose **Custom repositories**.
2. Add `https://github.com/BrawnyBravo/ha-watcher-companion` with type **Integration**.
3. Find **HA Watcher** in HACS, download it, and restart Home Assistant.

### Manual

Copy `custom_components/ha_watcher` into the `custom_components` folder of your Home
Assistant configuration and restart.

Requires Home Assistant 2026.3 or newer.

## Setup

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=ha_watcher)

1. **Settings > Devices & services > Add integration > HA Watcher** (or use the button).
2. Give it a name, for example the name of the machine the watcher runs on.
3. Leave **Only accept reports from the local network** on unless the watcher reaches
   Home Assistant over the internet.
4. Keep the **shared secret** that is filled in for you (a random one). With it, only
   reports signed by your watcher are accepted. It is required when local-only is off.
5. Home Assistant shows a webhook address such as
   `http://192.0.2.10:8123/api/webhook/3f5c...` and the shared secret. Copy them into the
   watcher's `home_assistant_push.url` and `home_assistant_push.secret` settings (see the
   ha-watcher documentation) and restart the watcher.

Keep the address and secret private. Without a secret, anyone who has the address can
send status reports, including fake "all OK" reports that would hide a dead watcher.
You can see the address again at any time under the integration's **Configure** button.

An entry without a shared secret (for example one added before setup offered it) keeps
working, but Home Assistant shows a repair recommending one until it is set.

**Local-only and reverse proxies.** Home Assistant decides "local" from the address the
request comes from. If Home Assistant sits behind a reverse proxy or tunnel on your network
(and is not set up to trust the proxy's forwarded client address), every request,
including ones from the internet, can look local. The local-only option then gives no
protection, so use a shared secret in any proxy or tunnel setup.

**Home Assistant's own log can contain the webhook address** (for example when a request
arrives from outside your network). Remove it before posting logs anywhere.

Add one entry per watcher if you run more than one.

### Options

| Option | Default | Meaning |
| --- | --- | --- |
| Offline timeout | 5 minutes (minimum 2) | Mark the watcher offline when no valid report has arrived for this long. Keep it at least twice the watcher's report interval. |
| Only accept reports from the local network | on | Reject reports that come from outside your network. Can be turned off only with a shared secret. Gives no protection behind a reverse proxy that hides the client address (see Setup). |
| Shared secret | random one from setup | Strongly recommended, at least 16 characters (spaces at either end are removed); the dialog suggests a random one. Required when local-only is off. When set, only reports signed with this secret are accepted (the watcher needs the same secret). Recommended when Home Assistant is reached over plain HTTP on a shared network, because anyone who sees one request could otherwise forge or replay reports. If the watcher restarts within the same second as its last report, or its clock steps backwards, its reports are refused until its clock passes the last accepted time; it recovers on its own. Signed reports may be up to 5 minutes behind and up to 1 minute ahead of Home Assistant's clock, so a watcher clock that ran fast and is then corrected is refused for at most a minute; with signing on, keep the offline timeout at least twice the report interval plus one minute. |

## Entities

Each watcher becomes one device with these entities:

| Entity | Type | Notes |
| --- | --- | --- |
| Watcher online | Connectivity | On while reports keep arriving. Off after the offline timeout. Unknown right after Home Assistant starts, until the first report or the timeout. |
| Home Assistant outage | Problem | On while the watcher considers Home Assistant to be in an outage. Attribute `since`. |
| Check *name* | Problem | One per watcher check (for example `Check api`, `Check backup`). On means failing; unknown means not run yet. Attributes `detail`, `failures`, `since`. New checks appear automatically, up to 32 per watcher in total (a safety limit). |
| Last report | Timestamp (diagnostic) | When Home Assistant last received a valid report. Attributes `sent_at`, `interval_s`, `watcher_version`. |
| Alerts sent (24h) | Number | Alerts the watcher sent in the last 24 hours. |
| Last power cycle | Timestamp | When the watcher last power-cycled Home Assistant's host. |
| Last recovery command | Timestamp | When the watcher last ran a recovery command. |

While the watcher is offline, the check, outage and count entities show as unavailable
(their data would be stale); **Watcher online** and **Last report** stay visible.
The last report is kept across restarts, but after a restart the check, outage and count
entities stay unavailable until the first fresh report arrives, so old data is never
shown as current.

**Retired checks.** A check the watcher no longer reports shows as unavailable. After the
next Home Assistant restart (or a reload of the integration) it is no longer provided, and
you can delete it from its entity settings. Deleting it also frees its place in the
32-check limit.

## Events

| Event | When |
| --- | --- |
| `ha_watcher_offline` | The watcher stopped reporting for the offline timeout (also when it never reports after Home Assistant starts). Fired once per outage, not again after a restart. |
| `ha_watcher_online` | Reports resumed after the watcher was offline. |

Event data: `entry_id`, `name`, `last_report` (ISO timestamp or `null`).

## Example automation: tell me when the watcher goes quiet

```yaml
alias: Watcher stopped reporting
triggers:
  - trigger: state
    entity_id: binary_sensor.ha_watcher_watcher_online
    to: "off"
actions:
  - action: notify.notify
    data:
      title: HA Watcher is offline
      message: >-
        No report from the watcher since
        {{ states('sensor.ha_watcher_last_report') | as_datetime | as_local }}.
        Home Assistant is not being watched until it is back.
mode: single
```

The same with the event, for every watcher you have:

```yaml
alias: Any watcher went offline
triggers:
  - trigger: event
    event_type: ha_watcher_offline
actions:
  - action: notify.notify
    data:
      message: "{{ trigger.event.data.name }} stopped reporting."
```

Entity ids depend on the name you chose; check them under the device page.

## For watcher developers

The report format is documented in [docs/PAYLOAD.md](docs/PAYLOAD.md) (contract v1).

## Troubleshooting

- **Watcher online stays unknown or off**: check the URL in the watcher's configuration,
  that the watcher box can reach Home Assistant on that address, and the Home Assistant
  log for "Rejected a status report". If the watcher reaches Home Assistant through a
  reverse proxy or from another network, turn off the local-network option (this needs
  a shared secret). Reports refused with 401 ("Rejected an unauthenticated status
  report") mean the watcher's secret differs from the one set here.
- **Diagnostics**: on the device page, **Download diagnostics**. The webhook address,
  shared secret, entry name, watcher id and check detail texts are removed from the file,
  and check names are replaced by `check_1`, `check_2` and so on.
  Still read it before attaching it to an issue.
- **Logs**: Home Assistant's log can contain the webhook address; remove it before sharing.

## Development

```bash
python3.14 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest --cov
```

Home Assistant's test tools do not run on Windows; use Linux, macOS or WSL.

## License

MIT, see [LICENSE](LICENSE).
