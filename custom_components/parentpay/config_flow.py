"""Config + options flow for ParentPay."""
from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import ParentPayClient
from .const import (
    CONF_PASSWORD,
    CONF_POLL_INTERVAL_MIN,
    CONF_POLL_WINDOW_END,
    CONF_POLL_WINDOW_START,
    CONF_PURCHASES_LIST_DEPTH,
    CONF_USERNAME,
    DEFAULT_POLL_INTERVAL_MIN,
    DEFAULT_POLL_WINDOW_END,
    DEFAULT_POLL_WINDOW_START,
    DEFAULT_PURCHASES_LIST_DEPTH,
    DOMAIN,
)
from .exceptions import ParentPayAuthError, ParentPayError

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class ParentPayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    _reauth_username: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = ParentPayClient(
                session,
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
            )
            try:
                await client.login()
            except ParentPayAuthError:
                errors["base"] = "invalid_auth"
            except (aiohttp.ClientError, ParentPayError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"ParentPay ({user_input[CONF_USERNAME]})",
                    data=user_input,
                )
        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        self._reauth_username = entry_data[CONF_USERNAME]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = ParentPayClient(
                session,
                username=self._reauth_username,  # type: ignore[arg-type]
                password=user_input[CONF_PASSWORD],
            )
            try:
                await client.login()
            except ParentPayAuthError:
                errors["base"] = "invalid_auth"
            else:
                entry = self._get_reauth_entry()
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return ParentPayOptionsFlow(config_entry)


class ParentPayOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self._entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_POLL_INTERVAL_MIN,
                    default=options.get(CONF_POLL_INTERVAL_MIN, DEFAULT_POLL_INTERVAL_MIN),
                ): vol.All(int, vol.Range(min=5, max=240)),
                vol.Optional(
                    CONF_POLL_WINDOW_START,
                    default=options.get(CONF_POLL_WINDOW_START, DEFAULT_POLL_WINDOW_START),
                ): str,
                vol.Optional(
                    CONF_POLL_WINDOW_END,
                    default=options.get(CONF_POLL_WINDOW_END, DEFAULT_POLL_WINDOW_END),
                ): str,
                vol.Optional(
                    CONF_PURCHASES_LIST_DEPTH,
                    default=options.get(CONF_PURCHASES_LIST_DEPTH, DEFAULT_PURCHASES_LIST_DEPTH),
                ): vol.All(int, vol.Range(min=1, max=50)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
