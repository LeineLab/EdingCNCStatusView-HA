"""The EdingCNC StatusView integration.

Emulates an EdingCNC StatusView LED display so the EdingCNC control
software can talk directly to Home Assistant over UDP, without an MQTT
broker or an external bridge script in between.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_AUTO_ADD,
    CONF_MANUAL_DEVICES,
    CONF_TIMEOUT,
    DEFAULT_AUTO_ADD,
    DEFAULT_TIMEOUT,
    DOMAIN,
)
from .protocol import StatusViewHub

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


def _option(entry: ConfigEntry, key: str, default):
    """Options set via the options flow win; fall back to the initial data."""
    return entry.options.get(key, entry.data.get(key, default))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up StatusView from a config entry."""
    port = int(entry.data[CONF_PORT])
    timeout = float(_option(entry, CONF_TIMEOUT, DEFAULT_TIMEOUT))
    auto_add = _option(entry, CONF_AUTO_ADD, DEFAULT_AUTO_ADD)
    manual_ips = set(_option(entry, CONF_MANUAL_DEVICES, []))

    hub = StatusViewHub(hass, port, timeout, auto_add, manual_ips)
    try:
        await hub.async_start()
    except OSError as err:
        raise ConfigEntryNotReady(
            f"Could not bind UDP port {port} for StatusView: {err}"
        ) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = hub

    entry.async_on_unload(
        async_track_time_interval(hass, hub.async_check_stale, timedelta(seconds=2))
    )
    entry.async_on_unload(hub.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply options changes (auto-add toggle, manually added IPs) live."""
    hub: StatusViewHub = hass.data[DOMAIN][entry.entry_id]
    hub.timeout = float(_option(entry, CONF_TIMEOUT, DEFAULT_TIMEOUT))
    hub.async_apply_options(
        _option(entry, CONF_AUTO_ADD, DEFAULT_AUTO_ADD),
        set(_option(entry, CONF_MANUAL_DEVICES, [])),
    )


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow deleting a StatusView device from the UI's device page."""
    hub: StatusViewHub = hass.data[DOMAIN][entry.entry_id]
    ip = next((ident[1] for ident in device_entry.identifiers if ident[0] == DOMAIN), None)
    if ip is None:
        return True

    hub.async_forget_device(ip)

    # A manually added IP shouldn't reappear on the next options-flow save.
    manual_ips = set(_option(entry, CONF_MANUAL_DEVICES, []))
    if ip in manual_ips:
        manual_ips.discard(ip)
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_MANUAL_DEVICES: sorted(manual_ips)}
        )
    return True
