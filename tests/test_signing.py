"""Optional shared-secret request signing."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

from aiohttp.test_utils import TestClient
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_watcher.const import (
    CONF_LOCAL_ONLY,
    CONF_OFFLINE_TIMEOUT,
    CONF_SHARED_SECRET,
    CONF_WEBHOOK_ID,
    DOMAIN,
    MAX_REPLAY_CACHE,
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


async def test_replay_cache_is_bounded(
    hass: HomeAssistant, signed_client: TestClient
) -> None:
    hub = hass.config_entries.async_get_entry("entry1").runtime_data
    now = time.time()
    for i in range(MAX_REPLAY_CACHE + 50):
        hub._remember_signature(f"sig{i}", now)
    assert len(hub._seen_signatures) <= MAX_REPLAY_CACHE
    # Expired entries are dropped first.
    hub._remember_signature("late", now + 1000)
    assert "sig0" not in hub._seen_signatures


async def test_unsigned_accepted_without_secret(
    hass: HomeAssistant, post, payload: dict[str, Any]
) -> None:
    """Without a secret, signature headers are not required."""
    assert (await post(payload)).status == 200
