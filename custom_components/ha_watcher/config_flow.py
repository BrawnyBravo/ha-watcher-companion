"""Config and options flows for HA Watcher."""

from __future__ import annotations

from collections.abc import Mapping
import secrets
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
    MIN_SECRET_LEN,
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


def _secret_errors(secret: str, local_only: bool) -> dict[str, str]:
    """Form errors for a (trimmed) shared secret and the local-only choice."""
    if secret and len(secret) < MIN_SECRET_LEN:
        return {CONF_SHARED_SECRET: "secret_too_short"}
    if not secret and not local_only:
        # Reachable from outside the network with only the URL as protection.
        return {CONF_SHARED_SECRET: "secret_required"}
    return {}


SECRET_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)


class HAWatcherConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a webhook that one ha-watcher service reports to."""

    VERSION = 1

    def __init__(self) -> None:
        # Signing is on by default: the secret field starts with a random one.
        self._suggested_secret = secrets.token_urlsafe(24)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a name, local-only and a shared secret (pre-filled)."""
        errors: dict[str, str] = {}
        values: Mapping[str, Any] = {
            CONF_NAME: DEFAULT_NAME,
            CONF_LOCAL_ONLY: DEFAULT_LOCAL_ONLY,
            CONF_SHARED_SECRET: self._suggested_secret,
        }
        if user_input is not None:
            secret = (user_input.get(CONF_SHARED_SECRET) or "").strip()
            local_only = user_input[CONF_LOCAL_ONLY]
            errors = _secret_errors(secret, local_only)
            if not errors:
                return self._create(user_input[CONF_NAME], local_only, secret)
            values = {**user_input, CONF_SHARED_SECRET: secret}

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=values[CONF_NAME]): str,
                    vol.Required(
                        CONF_LOCAL_ONLY, default=values[CONF_LOCAL_ONLY]
                    ): bool,
                    vol.Optional(
                        CONF_SHARED_SECRET,
                        description={"suggested_value": values[CONF_SHARED_SECRET]},
                    ): SECRET_SELECTOR,
                }
            ),
            errors=errors,
        )

    def _create(self, name: str, local_only: bool, secret: str) -> ConfigFlowResult:
        webhook_id = webhook.async_generate_id()
        placeholders = {"webhook_url": webhook_url(self.hass, webhook_id)}
        if secret:
            placeholders["shared_secret"] = secret
        return self.async_create_entry(
            title=name.strip() or DEFAULT_NAME,
            data={CONF_WEBHOOK_ID: webhook_id},
            options={
                CONF_LOCAL_ONLY: local_only,
                CONF_OFFLINE_TIMEOUT: DEFAULT_OFFLINE_TIMEOUT,
                CONF_SHARED_SECRET: secret,
            },
            # Picks the message under config.create_entry in strings.json.
            description="signed" if secret else "default",
            description_placeholders=placeholders,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HAWatcherOptionsFlow:
        """Return the options flow."""
        return HAWatcherOptionsFlow()


class HAWatcherOptionsFlow(OptionsFlowWithReload):
    """Timeout, local-only and optional shared secret; shows the webhook URL again."""

    def __init__(self) -> None:
        # One random suggestion per dialog, shown in the description. It is not
        # pre-filled here: saving another option would then switch signing on
        # and lock out a watcher that does not have the secret yet.
        self._suggested_secret = secrets.token_urlsafe(24)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        options: Mapping[str, Any] = self.config_entry.options
        if user_input is not None:
            secret = (user_input.get(CONF_SHARED_SECRET) or "").strip()
            errors = _secret_errors(secret, user_input[CONF_LOCAL_ONLY])
            if not errors:
                return self.async_create_entry(
                    data={
                        CONF_OFFLINE_TIMEOUT: int(user_input[CONF_OFFLINE_TIMEOUT]),
                        CONF_LOCAL_ONLY: user_input[CONF_LOCAL_ONLY],
                        CONF_SHARED_SECRET: secret,
                    }
                )
            options = user_input

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
                ): SECRET_SELECTOR,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "webhook_url": webhook_url(
                    self.hass, self.config_entry.data[CONF_WEBHOOK_ID]
                ),
                "suggested_secret": self._suggested_secret,
                "min_secret_len": str(MIN_SECRET_LEN),
            },
        )
