"""Test helpers."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er


def entity_id(
    hass: HomeAssistant, platform: str, key: str, entry_id: str = "entry1"
) -> str:
    """Look up an entity id by the integration's unique id scheme."""
    eid = er.async_get(hass).async_get_entity_id(
        platform, "ha_watcher", f"{entry_id}_{key}"
    )
    assert eid is not None, f"no {platform} entity for {key}"
    return eid
