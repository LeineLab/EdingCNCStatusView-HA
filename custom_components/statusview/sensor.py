"""Sensor platform for the EdingCNC StatusView integration."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_NEW_DEVICE
from .protocol import StatusViewDevice, StatusViewHub


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up StatusView sensors from a config entry."""
    hub: StatusViewHub = hass.data[DOMAIN][entry.entry_id]

    @callback
    def _add_device(device: StatusViewDevice) -> None:
        async_add_entities([StatusViewProgressSensor(hub, device)])

    for ip in hub.exposed:
        _add_device(hub.devices[ip])

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, _add_device))


class StatusViewProgressSensor(SensorEntity):
    """Job progress reported by the CNC controller, in percent."""

    _attr_has_entity_name = True
    _attr_name = "Progress"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_should_poll = False

    def __init__(self, hub: StatusViewHub, device: StatusViewDevice) -> None:
        self._hub = hub
        self._device = device
        self._attr_unique_id = f"{device.ip}_progress"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.ip)},
            name=f"CNC StatusView ({device.ip})",
            manufacturer="EdingCNC",
            model="StatusView",
        )

    @property
    def native_value(self) -> float:
        return round(self._device.percent, 1)

    @property
    def available(self) -> bool:
        return self._hub.is_available(self._device)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._device.signal_update, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
