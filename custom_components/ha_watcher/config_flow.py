"""Config and options flows for HA Watcher."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import webhook
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_LOCAL_ONLY,
    CONF_OFFLINE_TIMEOUT,
    CONF_SHARED_SECRET,
    CONF_WEBHOOK_ID,
    DEFAULT_LOCAL_ONLY,
    DEFAULT_NAME,
    DEFAULT_OFFLINE_TIMEOUT,
    DOMAIN,
    MAX_OFFLINE_TIMEOUT,
    MIN_OFFLINE_TIMEOUT,
)
from .hub import webhook_url

TIMEOUT_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=MIN_OFFLINE_TIMEOUT,
        max=MAX_OFFLINE_TIMEOUT,
        step=1,
        mode=selector.NumberSelectorMode.BOX,
        unit_of_measurement="min",
    )
)


class HAWatcherConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a webhook that one ha-watcher service reports to."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a name and whether the webhook accepts local requests only."""
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                        vol.Required(CONF_LOCAL_ONLY, default=DEFAULT_LOCAL_ONLY): bool,
                    }
                ),
            )

        webhook_id = webhook.async_generate_id()
        return self.async_create_entry(
            title=user_input[CONF_NAME].strip() or DEFAULT_NAME,
            data={CONF_WEBHOOK_ID: webhook_id},
            options={
                CONF_LOCAL_ONLY: user_input[CONF_LOCAL_ONLY],
                CONF_OFFLINE_TIMEOUT: DEFAULT_OFFLINE_TIMEOUT,
            },
            description_placeholders={
                "webhook_url": webhook_url(self.hass, webhook_id)
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HAWatcherOptionsFlow:
        """Return the options flow."""
        return HAWatcherOptionsFlow()


class HAWatcherOptionsFlow(OptionsFlowWithReload):
    """Offline timeout and local-only setting; also shows the webhook URL again."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_OFFLINE_TIMEOUT: int(user_input[CONF_OFFLINE_TIMEOUT]),
                    CONF_LOCAL_ONLY: user_input[CONF_LOCAL_ONLY],
                    CONF_SHARED_SECRET: user_input.get(CONF_SHARED_SECRET, "").strip(),
                }
            )

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_OFFLINE_TIMEOUT,
                    default=options.get(CONF_OFFLINE_TIMEOUT, DEFAULT_OFFLINE_TIMEOUT),
                ): TIMEOUT_SELECTOR,
                vol.Required(
                    CONF_LOCAL_ONLY,
                    default=options.get(CONF_LOCAL_ONLY, DEFAULT_LOCAL_ONLY),
                ): bool,
                vol.Optional(
                    CONF_SHARED_SECRET,
                    description={
                        "suggested_value": options.get(CONF_SHARED_SECRET, "")
                    },
                ): str,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={
                "webhook_url": webhook_url(
                    self.hass, self.config_entry.data[CONF_WEBHOOK_ID]
                )
            },
        )
