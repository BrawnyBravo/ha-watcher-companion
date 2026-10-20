"""Optional shared-secret request signing."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any
from unittest.mock import patch

from aiohttp.test_utils import TestClient
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_watcher.const import (
    CONF_LOCAL_ONLY,
    CONF_OFFLINE_TIMEOUT,
    CONF_SHARED_SECRET,
    CONF_WEBHOOK_ID,
    DOMAIN,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from .conftest import WEBHOOK_ID
from .helpers import entity_id

SECRET = "test-shared-secret-value"
URL = f"/api/webhook/{WEBHOOK_ID}"


def sign(body: bytes, secret: str = SECRET, ts: int | None = None) -> dict[str, str]:
    stamp = str(int(time.time()) if ts is None else ts)
    digest = hmac.new(
        secret.encode(), stamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-HA-Watcher-Timestamp": stamp,
        "X-HA-Watcher-Signature": f"sha256={digest}",
    }


@pytest.fixture
async def signed_client(hass: HomeAssistant, hass_client_no_auth) -> TestClient:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Watcher",
        data={CONF_WEBHOOK_ID: WEBHOOK_ID},
        options={
            CONF_LOCAL_ONLY: True,
            CONF_OFFLINE_TIMEOUT: 5,
            CONF_SHARED_SECRET: SECRET,
        },
        entry_id="entry1",
    )
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "webhook", {})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return await hass_client_no_auth()


async def test_signed_request_accepted(
    hass: HomeAssistant, signed_client: TestClient, payload: dict[str, Any]
) -> None:
    body = json.dumps(payload).encode()
    resp = await signed_client.post(URL, data=body, headers=sign(body))
    assert resp.status == 200
    online = hass.states.get(entity_id(hass, "binary_sensor", "online"))
    assert online.state == "on"


@pytest.mark.parametrize(
    "case",
    [
        "unsigned",
        "wrong_secret",
        "tampered_body",
        "old_timestamp",
        "future_timestamp",
        "bad_timestamp",
        "bad_signature_format",
    ],
)
async def test_bad_signatures_rejected(
    hass: HomeAssistant,
    signed_client: TestClient,
    payload: dict[str, Any],
    case: str,
) -> None:
    body = json.dumps(payload).encode()
    headers = sign(body)
    if case == "unsigned":
        headers = {"Content-Type": "application/json"}
    elif case == "wrong_secret":
        headers = sign(body, secret="other")
    elif case == "tampered_body":
        body = body.replace(b'"alerts_sent_24h": 2', b'"alerts_sent_24h": 0')
    elif case == "old_timestamp":
        headers = sign(body, ts=int(time.time()) - 301)
    elif case == "future_timestamp":
        headers = sign(body, ts=int(time.time()) + 301)
    elif case == "bad_timestamp":
        headers["X-HA-Watcher-Timestamp"] = "soon"
    elif case == "bad_signature_format":
        headers["X-HA-Watcher-Signature"] = headers["X-HA-Watcher-Signature"][7:]
    resp = await signed_client.post(URL, data=body, headers=headers)
    assert resp.status == 401
    assert await resp.json() == {"ok": False, "error": "unauthorized"}
    online = hass.states.get(entity_id(hass, "binary_sensor", "online"))
    assert online.state == "unknown"


async def test_replay_rejected_and_logged_once(
    hass: HomeAssistant,
    signed_client: TestClient,
    payload: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="custom_components.ha_watcher")
    body = json.dumps(payload).encode()
    headers = sign(body)
    assert (await signed_client.post(URL, data=body, headers=headers)).status == 200
    assert (await signed_client.post(URL, data=body, headers=headers)).status == 401
    assert (await signed_client.post(URL, data=body)).status == 401
    msgs = [
        r for r in caplog.records if "Rejected an unauthenticated" in r.getMessage()
    ]
    assert len(msgs) == 1
    assert SECRET not in caplog.text
    assert headers["X-HA-Watcher-Signature"] not in caplog.text


async def test_timestamp_must_increase(
    hass: HomeAssistant, signed_client: TestClient, payload: dict[str, Any]
) -> None:
    """A signed request must carry a newer timestamp than the last accepted one."""
    now = int(time.time())
    body = json.dumps(payload).encode()
    ok = await signed_client.post(URL, data=body, headers=sign(body, ts=now - 10))
    assert ok.status == 200
    payload["alerts_sent_24h"] = 3
    other = json.dumps(payload).encode()
    same_second = await signed_client.post(
        URL, data=other, headers=sign(other, ts=now - 10)
    )
    assert same_second.status == 401
    older = await signed_client.post(URL, data=other, headers=sign(other, ts=now - 20))
    assert older.status == 401
    newer = await signed_client.post(URL, data=other, headers=sign(other, ts=now - 5))
    assert newer.status == 200


async def test_replay_blocked_across_reload(
    hass: HomeAssistant,
    signed_client: TestClient,
    payload: dict[str, Any],
    hass_storage: dict[str, Any],
) -> None:
    """The last accepted timestamp is stored, so a replay after a reload fails."""
    body = json.dumps(payload).encode()
    headers = sign(body)
    assert (await signed_client.post(URL, data=body, headers=headers)).status == 200
    entry = hass.config_entries.async_get_entry("entry1")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    stored = hass_storage["ha_watcher.entry1"]["data"]
    assert stored["last_signed_ts"] == int(headers["X-HA-Watcher-Timestamp"])
    assert (await signed_client.post(URL, data=body, headers=headers)).status == 401


async def test_auth_warning_rearmed_after_good_report(
    hass: HomeAssistant,
    signed_client: TestClient,
    payload: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """After a good signed report, the next failure is logged again."""
    body = json.dumps(payload).encode()
    assert (await signed_client.post(URL, data=body)).status == 401
    assert (await signed_client.post(URL, data=body, headers=sign(body))).status == 200
    assert (await signed_client.post(URL, data=body)).status == 401
    msgs = [
        r for r in caplog.records if "Rejected an unauthenticated" in r.getMessage()
    ]
    assert len(msgs) == 2


async def test_unsigned_accepted_without_secret(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    """Without a secret, signature headers are not required."""
    assert (await post(payload)).status == 200


@pytest.mark.parametrize(
    ("offset", "status"),
    [
        (-300, 200),  # exactly at the lag limit
        (-301, 401),
        (60, 200),  # exactly at the ahead limit
        (61, 401),
        (300, 401),  # was accepted before the ahead window was narrowed
    ],
)
async def test_timestamp_window_boundaries(
    hass: HomeAssistant,
    signed_client: TestClient,
    payload: dict[str, Any],
    offset: int,
    status: int,
) -> None:
    """Up to 300 s behind and 60 s ahead of Home Assistant's clock is accepted."""
    now = 1_760_443_200
    body = json.dumps(payload).encode()
    with patch("custom_components.ha_watcher.hub.time") as fake_time:
        fake_time.time.return_value = now
        resp = await signed_client.post(
            URL, data=body, headers=sign(body, ts=now + offset)
        )
    assert resp.status == status


async def test_watcher_clock_step_back_lockout_is_short(
    hass: HomeAssistant, signed_client: TestClient, payload: dict[str, Any]
) -> None:
    """A watcher clock that ran ahead and is then corrected recovers within 60 s.

    That is shorter than the 2-minute minimum offline timeout, so a watcher that
    keeps reporting every minute is not marked offline by the correction.
    """
    now = 1_760_443_200
    body = json.dumps(payload).encode()
    online = entity_id(hass, "binary_sensor", "online")
    with patch("custom_components.ha_watcher.hub.time") as fake_time:
        fake_time.time.return_value = now
        # Watcher clock 60 s fast: accepted, and its stamp becomes the floor.
        ahead = await signed_client.post(
            URL, data=body, headers=sign(body, ts=now + 60)
        )
        assert ahead.status == 200
        # NTP corrects the watcher clock; reports within the next 60 s are refused.
        fake_time.time.return_value = now + 30
        refused = await signed_client.post(
            URL, data=body, headers=sign(body, ts=now + 30)
        )
        assert refused.status == 401
        # Once real time passes the floor, reports are accepted again.
        fake_time.time.return_value = now + 61
        back = await signed_client.post(URL, data=body, headers=sign(body, ts=now + 61))
        assert back.status == 200
    assert hass.states.get(online).state == "on"


async def test_remote_request_accepted_when_local_only_off(
    hass: HomeAssistant, hass_client_no_auth, payload: dict[str, Any]
) -> None:
    """With local-only off (which needs a secret), a signed remote report counts."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Watcher",
        data={CONF_WEBHOOK_ID: WEBHOOK_ID},
        options={
            CONF_LOCAL_ONLY: False,
            CONF_OFFLINE_TIMEOUT: 5,
            CONF_SHARED_SECRET: SECRET,
        },
        entry_id="entry1",
    )
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "webhook", {})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client_no_auth()
    body = json.dumps(payload).encode()
    with patch("homeassistant.util.network.is_local", return_value=False):
        unsigned = await client.post(URL, data=body)
        assert unsigned.status == 401
        signed = await client.post(URL, data=body, headers=sign(body))
        assert signed.status == 200
    online = hass.states.get(entity_id(hass, "binary_sensor", "online"))
    assert online.state == "on"
