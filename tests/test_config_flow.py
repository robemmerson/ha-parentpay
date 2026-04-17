"""Tests for the ParentPay config flow."""
from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant

from custom_components.parentpay.const import CONF_PASSWORD, CONF_USERNAME, DOMAIN


async def test_user_flow_happy_path(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.parentpay.config_flow.ParentPayClient.login",
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "u@example.com", CONF_PASSWORD: "pw"},
        )
        assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result2["data"][CONF_USERNAME] == "u@example.com"


async def test_user_flow_invalid_auth(hass: HomeAssistant) -> None:
    from custom_components.parentpay.exceptions import ParentPayAuthError

    with patch(
        "custom_components.parentpay.config_flow.ParentPayClient.login",
        side_effect=ParentPayAuthError("bad creds"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "u@example.com", CONF_PASSWORD: "bad"},
        )
        assert result2["type"] == data_entry_flow.FlowResultType.FORM
        assert result2["errors"]["base"] == "invalid_auth"
