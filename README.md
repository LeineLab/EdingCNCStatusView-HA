# EdingCNC StatusView → Home Assistant

Home Assistant integration that emulates an [EdingCNC StatusView](https://www.edingcnc.com/)
LED status display. The EdingCNC control software talks UDP directly to
Home Assistant — no MQTT broker, no bridge script, no extra hop.

## Installation

1. Copy `custom_components/statusview` into your Home Assistant
   `config/custom_components/` directory (or add this repo as a custom
   repository in HACS).
2. Restart Home Assistant.
3. Settings → Devices & Services → Add Integration → "EdingCNC StatusView".
   Pick the UDP port (default `30303`, matching the real device), an
   availability timeout, and whether new devices should be added
   automatically.
4. In the EdingCNC control software, set the StatusView IP address to your
   Home Assistant instance's IP address.

Each CNC controller that talks to Home Assistant shows up as its own
device (identified by source IP), with:

- `sensor.<device>_progress` — job progress in %
- `binary_sensor.<device>_on` / `idle` / `running` / `paused` / `error` / `buzzer` — the six output bits
- `binary_sensor.<device>_connected` — connectivity, diagnostic entity, based on the availability timeout

### Which IPs get a device?

Every packet is validated against the exact opcode/length combinations the
real protocol uses (see below) before anything happens — a port scan or
stray broadcast on `30303/udp` gets no reply and creates nothing, valid or
not. What happens with *valid* StatusView traffic from an IP Home Assistant
hasn't seen before depends on the "Automatically add new devices" setting
(Settings → Devices & Services → EdingCNC StatusView → Configure →
General settings):

- **On** (default): the first valid packet from a new IP immediately
  creates its device and entities.
- **Off**: valid traffic from an unknown IP is still answered correctly
  (the CNC controller never sees a dropped connection either way) but no
  device is created. Add it explicitly via Configure → "Add a device
  manually" — it offers a dropdown of IPs seen but not yet added, or you
  can type any IP address, even one that hasn't sent anything yet (its
  entities just stay unavailable until it does).

To remove a device, use the normal "Delete" button on its device page —
that also drops it from the manually-added list so it won't come back
until it's re-added or "Automatically add" is on.

## Protocol

All messages are sent from the CNC controller to whatever IP is configured
as the StatusView; the display never initiates anything. Every opcode is
answered immediately and unconditionally:

| From controller | Bytes | Meaning | Reply |
|---|---|---|---|
| `01 00 00 xx` | 4 | Ping | `03 08 00 00 01 00 07 00 01 01 01 00` (fixed firmware/info string) |
| `03 08 00 xx <numLeds> 00 00 00 00 00 <blinkLo> <blinkHi>` | 12 | Configure LED count / blink period | `01 00 00 00` (ACK) |
| `04 00 00 78` | 4 | Poll | `04 08 00 00 01 00 07 00 <outputStatus> 00 00 00` |
| `05 14 00 xx <outputStatus> 00 00 00 <blinkStatus> 00 00 00 <ledPos> 00 00 00 <rgb (LE)> <rgbReverse (LE)>` | 24 | Set LED position/color + output byte | `01 00 00 00` (ACK) |

`outputStatus` bit layout (as driven by the EdingCNC software in this
setup): bit0 On, bit1 Idle (inverted — 0 means idle), bit2 Running, bit3
Paused, bit4 Error, bit5 Buzzer.

## Investigation: "connection to StatusView lost"

The capture in `StatusView.pcapng` (real hardware, ~2h, WLAN) was used to
check whether the intermittent "connection lost" warnings from the
EdingCNC software point at a network or protocol problem.

Findings:

- Every single request in the capture got an immediate reply (2–10 ms),
  with **no dropped packets and no retransmissions** anywhere in ~66k
  frames.
- The capture contains 5 back-to-back UDP "sessions" (the controller PC
  picks a new ephemeral source port and does a full re-handshake: Ping →
  Config → Set). The sessions are contiguous — one starts within
  milliseconds of the previous one ending — which is exactly what you'd
  see if the controller software detected a stale connection and silently
  reopened it.
- Right before each of those 4 transitions, the PC-side idle heartbeat
  (normally one `01 00 00 xx` ping per second while idle) simply stops for
  **5–10 seconds**, then resumes with the *same* counter value it had
  before the gap, and a new session starts about a second later.

In other words: the gap is on the **controller PC's own outgoing
traffic**, not a dropped or delayed reply from the device side — the real
StatusView hardware never failed to answer anything in this capture. That
points at the EdingCNC software (or its host OS) briefly stalling its own
heartbeat thread — most plausibly while it's busy loading/preparing a job
— and then, once its watchdog notices the gap, deciding the StatusView
connection is dead and reopening it. That reopen is very likely what
triggers the "connection lost" message on screen. This isn't something a
StatusView emulator can fix from the receiving end; if EdingCNC exposes a
StatusView/heartbeat timeout setting, increasing it would be the direct
fix. Worth raising with EdingCNC support with this timing evidence.

One thing this *does* put on us: whatever answers these UDP requests must
reply as fast as the real hardware does, or it makes a marginal watchdog
timeout worse. This is why `custom_components/statusview/protocol.py`
always sends the UDP reply **before** touching any Home Assistant state
(entity updates are dispatched asynchronously afterwards) — unlike
`status.py`, which calls out to the MQTT broker synchronously for every
`Set` message before sending its ACK. If that MQTT publish is ever slow
(broker hiccup, network jitter, reconnect-in-progress), it delays the ACK
by however long the publish takes, which the real hardware never does.
The native integration's `binary_sensor.<device>_connected` entity gives
you a Home Assistant-side history of exactly when the controller went
quiet, to correlate against the software's own error log.
