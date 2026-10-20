"""Checks that the documentation matches what the code and the watcher accept."""

from __future__ import annotations

import json
from pathlib import Path
import re

ROOT = Path(__file__).parent.parent

# The setting names of the ha-watcher service's `home_assistant_push` section
# (HomeAssistantPushConfig in ha-watcher's config.py). Update both together.
WATCHER_PUSH_KEYS = {"url", "every_seconds", "timeout_seconds", "secret", "watcher_id"}


def test_payload_doc_uses_the_watcher_setting_names() -> None:
    """The copyable watcher example names only keys the watcher really has."""
    text = (ROOT / "docs" / "PAYLOAD.md").read_text(encoding="utf-8")
    block = re.search(r"```yaml\nhome_assistant_push:\n(.*?)```", text, re.DOTALL)
    assert block is not None
    keys = set(re.findall(r"^  ([a-z_]+):", block.group(1), re.MULTILINE))
    assert keys == WATCHER_PUSH_KEYS


def test_local_only_warns_about_reverse_proxies() -> None:
    """Users behind a proxy are told local-only may not protect them."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    proxy = next(
        p for p in readme.split("\n\n") if "Local-only and reverse proxies" in p
    )
    assert "no\nprotection" in proxy or "no protection" in proxy
    assert "shared secret" in proxy
    strings = json.loads(
        (ROOT / "custom_components" / "ha_watcher" / "strings.json").read_text(
            encoding="utf-8"
        )
    )
    for flow, step in (("config", "user"), ("options", "init")):
        text = strings[flow]["step"][step]["data_description"]["local_only"]
        assert "reverse proxy" in text
        assert "secret" in text


def test_translations_match_strings() -> None:
    folder = ROOT / "custom_components" / "ha_watcher"
    strings = json.loads((folder / "strings.json").read_text(encoding="utf-8"))
    english = json.loads(
        (folder / "translations" / "en.json").read_text(encoding="utf-8")
    )
    assert english == strings


def test_payload_doc_says_the_secret_is_trimmed() -> None:
    """Senders must trim the secret like the integration does, or every report fails."""
    text = (ROOT / "docs" / "PAYLOAD.md").read_text(encoding="utf-8")
    signing = text.split("## Signing", 1)[1]
    assert "Spaces and line breaks at either end are\n  removed" in signing
