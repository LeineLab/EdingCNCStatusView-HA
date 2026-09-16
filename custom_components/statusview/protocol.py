"""Emulation of an EdingCNC StatusView LED display over UDP.

Protocol reverse engineered from a capture of the real hardware talking to
the EdingCNC control software (see StatusView.pcapng in the repo root).
Every request from the controller is answered immediately, before any
Home Assistant state is touched, since the real hardware replies within a
few milliseconds and the controller appears to apply a fairly tight
watchdog on that response time (see the "connection lost" investigation
in README.md).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import SIGNAL_NEW_DEVICE, SIGNAL_UPDATE_PREFIX

_LOGGER = logging.getLogger(__name__)

OPCODE_PING = 0x01
OPCODE_CONFIG = 0x03
OPCODE_POLL = 0x04
OPCODE_SET = 0x05

# Exact payload length the real controller uses for each opcode (see the
# capture). A packet is only ever treated as a StatusView request - and only
# ever allowed to create a new device - if it matches one of these exactly.
# Anything else (a port scan, a stray broadcast, random noise on the LAN) is
# silently dropped: no reply, no device, no entities.
KNOWN_REQUEST_LENGTHS = {
    OPCODE_PING: 4,
    OPCODE_CONFIG: 12,
    OPCODE_POLL: 4,
    OPCODE_SET: 24,
}

# Fixed replies observed in the capture - the real hardware never varies these.
ACK = bytes.fromhex("01000000")
FIRMWARE_INFO = bytes.fromhex("030800000100070001010100")


def _is_known_request(data: bytes) -> bool:
    if not data:
        return False
    return KNOWN_REQUEST_LENGTHS.get(data[0]) == len(data)


def _poll_response(output_status: int) -> bytes:
    return bytes(
        (0x04, 0x08, 0x00, 0x00, 0x01, 0x00, 0x07, 0x00, output_status & 0xFF, 0x00, 0x00, 0x00)
    )


@dataclass
class StatusViewDevice:
    """Runtime state of one StatusView client (one CNC controller)."""

    ip: str
    num_leds: int = 0
    blink_time_ms: int = 0
    output_status: int = 0
    blink_status: int = 0
    led_pos: int = 0
    rgb: int = 0
    rgb_reverse: int = 0
    percent: float = 0.0
    last_seen: float = field(default_factory=time.monotonic)

    @property
    def signal_update(self) -> str:
        return f"{SIGNAL_UPDATE_PREFIX}_{self.ip}"

    def output_bit(self, bit: int) -> bool:
        return bool(self.output_status & (1 << bit))


class StatusViewProtocol(asyncio.DatagramProtocol):
    """asyncio server that speaks the StatusView wire protocol."""

    def __init__(self, hass: HomeAssistant, hub: "StatusViewHub") -> None:
        self.hass = hass
        self.hub = hub
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if not _is_known_request(data):
            # Not a StatusView request (wrong opcode, wrong length for that
            # opcode, or empty) - e.g. a port scanner or stray broadcast.
            # Deliberately a no-op: no reply, no device/entities created.
            _LOGGER.debug("Ignoring non-StatusView packet from %s: %s", addr[0], data.hex())
            return

        ip = addr[0]
        device = self.hub.devices.get(ip)
        if device is None:
            device = StatusViewDevice(ip=ip)
            self.hub.devices[ip] = device

        device.last_seen = time.monotonic()
        opcode = data[0]
        reply: bytes

        if opcode == OPCODE_PING:
            reply = FIRMWARE_INFO
        elif opcode == OPCODE_CONFIG:
            device.num_leds = data[4]
            device.blink_time_ms = data[10] | (data[11] << 8)
            reply = ACK
        elif opcode == OPCODE_POLL:
            reply = _poll_response(device.output_status)
        else:  # OPCODE_SET
            device.output_status = data[4]
            device.blink_status = data[8]
            device.led_pos = data[12]
            device.rgb = int.from_bytes(data[16:20], "little")
            device.rgb_reverse = int.from_bytes(data[20:24], "little")
            led_pos = device.led_pos
            rgb = device.rgb
            if device.rgb_reverse:
                led_pos = device.num_leds - led_pos
                rgb = device.rgb_reverse
            device.rgb = rgb
            device.percent = (led_pos * 100 / device.num_leds) if device.num_leds else 0.0
            reply = ACK

        # Reply first - do this before any Home Assistant bookkeeping so the
        # controller's response-time watchdog never has to wait on us. Note
        # this happens unconditionally: even a device that isn't (yet)
        # exposed as HA entities still gets a correct protocol reply, so a
        # disabled "auto add" never makes the CNC controller think the
        # StatusView is unreachable.
        if self.transport is not None:
            self.transport.sendto(reply, addr)

        self.hub.async_maybe_expose(ip)
        async_dispatcher_send(self.hass, device.signal_update)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.warning("StatusView UDP error: %s", exc)


class StatusViewHub:
    """Owns the UDP listener socket and tracks known StatusView clients.

    Every IP address that ever sent a valid request has a `StatusViewDevice`
    in `devices` and gets a correct protocol reply - that part is unaffected
    by configuration, so the CNC controller is never left hanging. Whether a
    device is also *exposed* as Home Assistant entities is a separate,
    reconfigurable decision tracked in `exposed`, so an unattended install
    doesn't grow a phantom device for every stray packet on the LAN.
    """

    def __init__(
        self, hass: HomeAssistant, port: int, timeout: float, auto_add: bool, manual_ips: set[str]
    ) -> None:
        self.hass = hass
        self.port = port
        self.timeout = timeout
        self.auto_add = auto_add
        self.manual_ips = set(manual_ips)
        self.devices: dict[str, StatusViewDevice] = {}
        self.exposed: set[str] = set()
        self._transport: asyncio.DatagramTransport | None = None
        self._last_availability: dict[str, bool] = {}

    async def async_start(self) -> None:
        loop = self.hass.loop
        transport, _protocol = await loop.create_datagram_endpoint(
            lambda: StatusViewProtocol(self.hass, self),
            local_addr=("0.0.0.0", self.port),
        )
        self._transport = transport
        for ip in self.manual_ips:
            self.devices.setdefault(ip, StatusViewDevice(ip=ip))
            self.async_maybe_expose(ip)

    def async_stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def is_available(self, device: StatusViewDevice) -> bool:
        return (time.monotonic() - device.last_seen) < self.timeout

    @property
    def pending_ips(self) -> list[str]:
        """IPs that sent valid traffic but aren't exposed as entities yet."""
        return sorted(set(self.devices) - self.exposed)

    @callback
    def async_maybe_expose(self, ip: str) -> None:
        """Create entities for `ip` if it qualifies and isn't exposed yet."""
        if ip in self.exposed:
            return
        if not (self.auto_add or ip in self.manual_ips):
            return
        self.exposed.add(ip)
        async_dispatcher_send(self.hass, SIGNAL_NEW_DEVICE, self.devices[ip])

    @callback
    def async_apply_options(self, auto_add: bool, manual_ips: set[str]) -> None:
        """Re-evaluate exposure after the config entry's options changed."""
        self.auto_add = auto_add
        self.manual_ips = set(manual_ips)
        for ip in self.manual_ips:
            self.devices.setdefault(ip, StatusViewDevice(ip=ip))
        for ip in list(self.devices):
            self.async_maybe_expose(ip)

    @callback
    def async_forget_device(self, ip: str) -> None:
        """Drop a device entirely, e.g. after it's deleted from the UI."""
        self.devices.pop(ip, None)
        self.exposed.discard(ip)
        self.manual_ips.discard(ip)
        self._last_availability.pop(ip, None)

    @callback
    def async_check_stale(self, _now=None) -> None:
        """Re-evaluate availability so devices drop offline even without new packets."""
        for ip, device in self.devices.items():
            now_available = self.is_available(device)
            if self._last_availability.get(ip) != now_available:
                self._last_availability[ip] = now_available
                async_dispatcher_send(self.hass, device.signal_update)
