"""Checks that the documentation matches what the code and the watcher accept."""

from __future__ import annotations

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
