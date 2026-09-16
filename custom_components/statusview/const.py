"""Constants for the EdingCNC StatusView integration."""

DOMAIN = "statusview"

DEFAULT_PORT = 30303
DEFAULT_TIMEOUT = 10  # seconds without a packet before a device is marked unavailable
DEFAULT_AUTO_ADD = True

CONF_TIMEOUT = "timeout"
CONF_AUTO_ADD = "auto_add"
CONF_MANUAL_DEVICES = "manual_devices"
CONF_NEW_DEVICE_IP = "ip_address"

SIGNAL_NEW_DEVICE = f"{DOMAIN}_new_device"
SIGNAL_UPDATE_PREFIX = f"{DOMAIN}_update"
