# Status payload contract, version 1

This is the message the [ha-watcher](https://github.com/BrawnyBravo/ha-watcher) service
sends to the HA Watcher integration. It is the only interface between the two: the
watcher implements the sending side from this document, and the integration validates
every message against it.

## Transport

| Item | Value |
| --- | --- |
| Method | `POST` (anything else gets `405 Method Not Allowed`) |
| URL | The webhook address shown when the integration is added, for example `http://192.0.2.10:8123/api/webhook/<64 hex characters>` |
| Body | One JSON object, UTF-8 |
| `Content-Type` | `application/json` (recommended; the body is parsed as JSON either way) |
| Authentication | None besides the webhook id in the URL. Treat the URL as a secret. |
| Network | By default Home Assistant only accepts the request from a local network address. If it arrives from elsewhere it is silently ignored (HTTP 200, no effect). The user can turn this off in the integration options. |

### Recommended watcher configuration

```yaml
home_assistant_push:
  url: "http://192.0.2.10:8123/api/webhook/..."  # pasted from the integration
  interval: 60   # seconds between reports; also sent as interval_s
  timeout: 10    # seconds per request
```

The key names above are a suggestion for the watcher side; only the payload below is
part of the contract.

### When to send

- Every `interval_s` seconds, whether anything changed or not. The integration marks the
  watcher offline when no valid report arrives within the user's offline timeout
  (default 5 minutes, minimum 2), so `interval_s` should be well under half of it.
  60 seconds is a good default.
- Optionally also immediately after a check changes state, so Home Assistant sees it
  without waiting for the next interval.
- Sending must never block or delay the watcher's own checks and alerts. Use a short
  timeout and drop the report on any error. Home Assistant being unreachable is exactly
  the situation the watcher exists for, so a failed push is normal and must not be
  alerted on by itself.

### Responses

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | `{"ok": true}` | Accepted. |
| `400` | `{"ok": false, "error": "invalid payload"}` | Not JSON, or does not match this contract. Nothing was changed. Home Assistant logs one warning naming the first bad field (never the values) until a valid report arrives. |
| `200` with an empty body | | The webhook id is unknown (integration removed), or a remote request reached a local-only webhook. Home Assistant answers 200 on purpose so the URL cannot be probed. |
| `405` | | Not a POST. |

The watcher may log a `400` once, but must not retry the same payload.

## Body

```json
{
  "v": 1,
  "watcher_id": "watcher-box-1",
  "version": "1.4.0",
  "sent_at": "2026-10-13T12:00:00+00:00",
  "interval_s": 60,
  "checks": {
    "api":    {"ok": true,  "since": "2026-10-13T11:00:00+00:00", "detail": "API OK in 40 ms", "failures": 0},
    "backup": {"ok": false, "since": "2026-10-12T03:00:00+00:00", "detail": "Newest backup is 2d old (limit 26h)", "failures": 3},
    "host":   {"ok": null,  "since": null, "detail": "not run yet", "failures": 0}
  },
  "outage": {"active": false, "since": null},
  "alerts_sent_24h": 2,
  "last_power_cycle": "2026-10-10T08:30:00+00:00",
  "last_recovery_command": null
}
```

All top-level keys are **required**. Unknown keys anywhere are ignored, so later
versions can add fields without breaking older integrations.

### Top level

| Key | Type | Rules | Meaning |
| --- | --- | --- | --- |
| `v` | integer | `>= 1` | Contract version. Send `1`. Integrations accept any higher number as long as the v1 keys are present and valid. |
| `watcher_id` | string | 1-128 characters (longer is cut) | Stable id of this watcher install, for example a host name or a random id saved on first run. Not shown as an entity; redacted from diagnostics. |
| `version` | string | up to 128 characters | ha-watcher software version. Shown as the device's software version. |
| `sent_at` | timestamp | see Timestamps | When the watcher built this report. Shown as an attribute only; the integration uses its own receive time for "Last report" so clock drift on the watcher box cannot cause false offline alerts. |
| `interval_s` | integer | `>= 1` | Seconds until the next scheduled report. If twice this is more than the offline timeout, Home Assistant logs one warning. |
| `checks` | object | at most 200 entries | One entry per check, keyed by check name. See Checks. |
| `outage` | object | | See Outage. |
| `alerts_sent_24h` | integer | `>= 0` | Number of alerts the watcher sent in the last 24 hours. |
| `last_power_cycle` | timestamp or `null` | | When the watcher last power-cycled Home Assistant's host; `null` if never or unknown. |
| `last_recovery_command` | timestamp or `null` | | When the watcher last ran a recovery command (for example a remote restart); `null` if never or unknown. |

### Checks

The object key is the check name: a string of 1-128 characters. Use the watcher's own
result keys as they are, for example `api`, `host`, `core`, `core:safe_mode`, `backup`,
`supervisor:health`, `memory` or `entity:sensor.example`. Each name becomes one problem
sensor in Home Assistant, named "Check `<name>`"; a name seen for the first time creates a
new entity, and a name that stops appearing makes its entity unavailable (it is not
deleted). **Keep names stable**: renaming a check creates a new entity.

| Key | Type | Rules | Meaning |
| --- | --- | --- | --- |
| `ok` | `true`, `false` or `null` | must be a JSON boolean or null | `true` passing, `false` failing, `null` not known yet (not run, skipped). Shown as problem off / on / unknown. |
| `since` | timestamp or `null` | | When the check entered its current `ok` state. |
| `detail` | string | longer than 1024 characters is cut | Human-readable result, for example `Newest backup is 2d old`. Must not contain tokens, passwords or other secrets: it is shown in Home Assistant and stored in its database. |
| `failures` | integer | `>= 0` | Consecutive failed runs (0 when passing). |

### Outage

| Key | Type | Meaning |
| --- | --- | --- |
| `active` | boolean | `true` while the watcher considers Home Assistant to be in an outage (alerts sent, recovery steps may run). |
| `since` | timestamp or `null` | When the current outage started; `null` when none is active. |

Note: reports usually cannot arrive while Home Assistant is fully down. The outage flag
is still useful for partial outages (for example the API is slow or an add-on stopped)
and for the first report after recovery.

### Timestamps

ISO 8601 strings **with a timezone**, for example `2026-10-13T12:00:00+00:00` or
`2026-10-13T12:00:00Z`. Times without a timezone are rejected. The integration converts
every timestamp to UTC. Send UTC (`datetime.now(timezone.utc).isoformat()` in Python).

### Strict types

The integration does not coerce types: `"60"` is not an integer, `1` is not a boolean,
and `true` is not an integer. Use native JSON types.

## Example sender (Python, standard library only)

```python
import json
import urllib.request
from datetime import datetime, timezone


def push(url: str, payload: dict, timeout: float = 10) -> None:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
    except Exception:
        pass  # Home Assistant may be down; that is what the watcher is for.


payload = {
    "v": 1,
    "watcher_id": "watcher-box-1",
    "version": "1.4.0",
    "sent_at": datetime.now(timezone.utc).isoformat(),
    "interval_s": 60,
    "checks": {"api": {"ok": True, "since": None, "detail": "API OK", "failures": 0}},
    "outage": {"active": False, "since": None},
    "alerts_sent_24h": 0,
    "last_power_cycle": None,
    "last_recovery_command": None,
}
```

## Versioning

- Adding optional keys does not change `v`.
- A change that removes or redefines a v1 key would be `v: 2`. Integrations at this
  version accept `v: 2` reports only if they still contain valid v1 keys, so a watcher
  should keep sending the v1 keys for as long as older integrations are in use.
