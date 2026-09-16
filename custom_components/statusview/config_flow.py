"""Config and options flow for the EdingCNC StatusView integration."""
from __future__ import annotations

import ipaddress
import socket
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PORT
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_AUTO_ADD,
    CONF_MANUAL_DEVICES,
    CONF_NEW_DEVICE_IP,
    CONF_TIMEOUT,
    DEFAULT_AUTO_ADD,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    DOMAIN,
)

# Plain int+Range fields get auto-rendered as a slider that only shows its
# value while being dragged. A "box" NumberSelector is a normal numeric
# input instead, so the current value is always visible and can be typed.
PORT_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(min=1, max=65535, step=1, mode=selector.NumberSelectorMode.BOX)
)
TIMEOUT_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=2, max=120, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="s"
    )
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PORT, default=DEFAULT_PORT): PORT_SELECTOR,
        vol.Required(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): TIMEOUT_SELECTOR,
        vol.Required(CONF_AUTO_ADD, default=DEFAULT_AUTO_ADD): bool,
    }
)


def _port_is_free(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


class StatusViewConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for StatusView."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial setup step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_PORT] = int(user_input[CONF_PORT])
            if await self.hass.async_add_executor_job(_port_is_free, user_input[CONF_PORT]):
                return self.async_create_entry(title="EdingCNC StatusView", data=user_input)
            errors["base"] = "port_in_use"

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> StatusViewOptionsFlow:
        return StatusViewOptionsFlow(config_entry)


class StatusViewOptionsFlow(config_entries.OptionsFlow):
    """Toggle auto-add and manually add/pre-register StatusView devices."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    def _current(self, key: str, default: Any) -> Any:
        return self.config_entry.options.get(key, self.config_entry.data.get(key, default))

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(step_id="init", menu_options=["settings", "add_device"])

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data={**self.config_entry.options, **user_input})

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_TIMEOUT, default=self._current(CONF_TIMEOUT, DEFAULT_TIMEOUT)
                ): TIMEOUT_SELECTOR,
                vol.Required(
                    CONF_AUTO_ADD, default=self._current(CONF_AUTO_ADD, DEFAULT_AUTO_ADD)
                ): bool,
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    async def async_step_add_device(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            ip = user_input[CONF_NEW_DEVICE_IP].strip()
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                errors["base"] = "invalid_ip"
            else:
                manual_ips = set(self._current(CONF_MANUAL_DEVICES, []))
                manual_ips.add(ip)
                return self.async_create_entry(
                    title="",
                    data={**self.config_entry.options, CONF_MANUAL_DEVICES: sorted(manual_ips)},
                )

        hub = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        pending_ips = hub.pending_ips if hub else []
        ip_field = (
            selector.SelectSelector(
                selector.SelectSelectorConfig(options=pending_ips, custom_value=True, sort=True)
            )
            if pending_ips
            else selector.TextSelector()
        )
        schema = vol.Schema({vol.Required(CONF_NEW_DEVICE_IP): ip_field})
        return self.async_show_form(
            step_id="add_device",
            data_schema=schema,
            errors=errors,
            description_placeholders={"pending": ", ".join(pending_ips) or "-"},
        )
