"""Binary sensor platform for the EdingCNC StatusView integration."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_NEW_DEVICE
from .protocol import StatusViewDevice, StatusViewHub


@dataclass(frozen=True, kw_only=True)
class StatusViewBinarySensorDescription(BinarySensorEntityDescription):
    """Describes one of the six StatusView output bits."""

    bit: int
    invert: bool = False


# Bit layout of the "outputStatus" byte, as reverse engineered from the
# EdingCNC control software's Set command.
OUTPUT_DESCRIPTIONS: tuple[StatusViewBinarySensorDescription, ...] = (
    StatusViewBinarySensorDescription(
        key="on", name="On", device_class=BinarySensorDeviceClass.POWER, bit=0
    ),
    StatusViewBinarySensorDescription(
        key="idle",
        name="Idle",
        device_class=BinarySensorDeviceClass.MOVING,
        bit=1,
        invert=True,
    ),
    StatusViewBinarySensorDescription(
        key="running", name="Running", device_class=BinarySensorDeviceClass.RUNNING, bit=2
    ),
    StatusViewBinarySensorDescription(key="paused", name="Paused", bit=3),
    StatusViewBinarySensorDescription(
        key="error", name="Error", device_class=BinarySensorDeviceClass.PROBLEM, bit=4
    ),
    StatusViewBinarySensorDescription(
        key="buzzer", name="Buzzer", device_class=BinarySensorDeviceClass.SOUND, bit=5
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up StatusView binary sensors from a config entry."""
    hub: StatusViewHub = hass.data[DOMAIN][entry.entry_id]

    @callback
    def _add_device(device: StatusViewDevice) -> None:
        entities: list[BinarySensorEntity] = [
            StatusViewOutputBinarySensor(hub, device, description)
            for description in OUTPUT_DESCRIPTIONS
        ]
        entities.append(StatusViewConnectedBinarySensor(hub, device))
        async_add_entities(entities)

    for ip in hub.exposed:
        _add_device(hub.devices[ip])

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, _add_device))


def _device_info(device: StatusViewDevice) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, device.ip)},
        name=f"CNC StatusView ({device.ip})",
        manufacturer="EdingCNC",
        model="StatusView",
    )


class StatusViewOutputBinarySensor(BinarySensorEntity):
    """One bit of the StatusView outputStatus byte."""

    entity_description: StatusViewBinarySensorDescription
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        hub: StatusViewHub,
        device: StatusViewDevice,
        description: StatusViewBinarySensorDescription,
    ) -> None:
        self.entity_description = description
        self._hub = hub
        self._device = device
        self._attr_unique_id = f"{device.ip}_{description.key}"
        self._attr_device_info = _device_info(device)

    @property
    def is_on(self) -> bool:
        bit = self._device.output_bit(self.entity_description.bit)
        return not bit if self.entity_description.invert else bit

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


class StatusViewConnectedBinarySensor(BinarySensorEntity):
    """Whether the CNC controller has been heard from recently.

    Stays available itself (it IS the connectivity indicator) while the
    other entities for this device go unavailable once it turns off.
    """

    _attr_has_entity_name = True
    _attr_name = "Connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, hub: StatusViewHub, device: StatusViewDevice) -> None:
        self._hub = hub
        self._device = device
        self._attr_unique_id = f"{device.ip}_connected"
        self._attr_device_info = _device_info(device)

    @property
    def is_on(self) -> bool:
        return self._hub.is_available(self._device)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._device.signal_update, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
