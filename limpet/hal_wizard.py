"""wq#4506 -- HAL device-event subscriber + agent-chat device wizard.

When a target device is plugged into the LIMPET appliance, the operator should
not have to remember which verb each piece of hardware supports. This module
turns a hot-plug event into a numbered wizard prompt ("1) Dump flash contents,
2) Attach a debugger, ...") and hands it to the agent conversation so the
operator can answer with a single number.

Wiring (what this subscribes to, and why not literally what the ticket said)
---------------------------------------------------------------------------
wq#4506 asked for a subscription to ``/run/limpet/hal.sock`` on a
``device_connected`` event, injected through an agent loop's ``inject_message()``
per "agent-processor-spec.md section 7". Those names describe an earlier plan,
not the built artifacts, so this module wires to what the HAL daemon actually
exposes (``ops/wq3809-limpet-hal/hal_daemon.py``, wq#4112):

  * ``/run/limpet/hal.sock`` is the udev INPUT socket (``SOCK_DGRAM``); the
    daemon PUBLISHES device changes on a separate stream socket,
    ``/run/limpet/hal-events.sock`` (``SOCK_STREAM``), one JSON line per event
    (``hal_daemon.py`` ``_notify_subscribers``). We subscribe to that stream.
  * the event the daemon emits on hot-plug is ``{"type": "device_added",
    "device": <entry>, "timestamp": <float>}`` -- not ``device_connected``.
    ``<entry>`` is a ``hal.detect()`` row: ``name``, ``hw_type``,
    ``capabilities`` (the real verb vocabulary), ``usb_id``, ``descriptor``,
    ``device_node``.
  * there is no agent loop / ``inject_message()`` in the tree yet -- the agent
    chat session service is deferred to a provisioning ticket
    (``docs/agent-chat-spec.md`` section 9). So ``inject_message`` here is an
    injected ``Callable[[str], None]``: the concrete hook point the session
    service calls into when it is built, following the ISSUE-LOG #1327
    precedent of delivering the integration hook rather than blocking on an
    integration target that does not exist. See kb/ISSUE-LOG.md row 1516.

The socket path is overridable (``LIMPET_HAL_EVENT_SOCKET``) and defaults to the
same value the daemon binds, so the two agree without hand-wiring.
"""
from __future__ import annotations

import json
import os
import socket
from typing import Callable, Iterable, Iterator

#: Must match ``hal_daemon.EVENT_SOCKET`` so subscriber and publisher agree.
DEFAULT_EVENT_SOCKET = os.environ.get(
    "LIMPET_HAL_EVENT_SOCKET", "/run/limpet/hal-events.sock"
)

#: The event type the HAL daemon emits when a device appears (hal_daemon.py).
EVENT_DEVICE_ADDED = "device_added"

# Map each real HAL capability token (hal.py plugin `capabilities` tuples) to the
# operator-facing wizard action it unlocks. Several capabilities fold into one
# action (spi_flash_read/write + eeprom_program are all "flash" work), so the
# wizard lists each action once, in the order first seen below.
CAPABILITY_ACTIONS: list[tuple[tuple[str, ...], str]] = [
    (("spi_flash_read",), "Dump flash contents"),
    (("spi_flash_write", "eeprom_program", "minipro"), "Program / write flash"),
    (("edl_flash", "maskrom_flash", "adb"), "Read device partitions / flash image"),
    (("jtag", "swd", "openocd_target", "gdb_server"), "Attach a debugger (JTAG/SWD/GDB)"),
    (("logic_capture", "protocol_decode", "pulseview"), "Capture a logic / protocol trace"),
    (("i2c", "spi", "uart", "generic_bus_bridge"), "Bridge to an on-board bus (I2C/SPI/UART)"),
    (("rf_capture", "iq_sample", "rtl_sdr"), "Capture an RF / IQ sample"),
    (("visual_input", "screen_reading", "kvm"), "Read the target's screen (KVM / capture)"),
    (("image_classification", "on_device_ml", "edgetpu_inference"), "Run an on-device ML inference"),
]


def wizard_options(device: dict) -> list[str]:
    """Ordered, de-duplicated list of wizard action labels for one device.

    Capability-derived actions come first (only those the device actually
    advertises), then two actions available for any device: identify, and a
    free-form escape hatch so the numbered menu never traps the operator.
    """
    caps = set(device.get("capabilities") or ())
    options: list[str] = []
    for tokens, label in CAPABILITY_ACTIONS:
        if caps.intersection(tokens) and label not in options:
            options.append(label)
    options.append("Identify this hardware in detail")
    options.append("Something else (describe what you want to do)")
    return options


def build_wizard_prompt(device: dict) -> str:
    """Render the numbered device wizard prompt for a ``device_added`` event.

    `device` is the ``device`` payload of the daemon event (a `hal.detect()`
    row). Missing fields degrade gracefully -- a device with no name or no
    capabilities still yields a usable menu.
    """
    name = device.get("name") or "Unknown device"
    usb_id = device.get("usb_id") or "unknown"
    hw_type = device.get("hw_type") or "unknown"
    descriptor = device.get("descriptor")

    lines = [
        f"A device was just connected: {name} (USB {usb_id}, type {hw_type}).",
    ]
    if descriptor:
        lines.append(f"USB descriptor: {descriptor}")
    lines.append("What would you like to do? Reply with a number:")
    for i, label in enumerate(wizard_options(device), start=1):
        lines.append(f"  {i}) {label}")
    return "\n".join(lines)


def iter_json_lines(reader: Iterable[bytes]) -> Iterator[dict]:
    """Yield one decoded JSON object per newline-delimited line from `reader`.

    Malformed lines (the daemon's stream is best-effort) and blank lines are
    skipped rather than aborting the subscription.
    """
    for raw in reader:
        line = raw.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue


def _socket_lines(sock: socket.socket) -> Iterator[bytes]:
    """Turn a connected stream socket into an iterator of newline-split chunks."""
    buf = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            if buf:
                yield buf
            return
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line


class HalEventSubscriber:
    """Subscribes to the HAL event stream and injects a wizard on hot-plug.

    `inject_message` is the agent-conversation hook: the session service passes
    its own ``inject_message`` here; tests pass a list's ``append``. Only
    ``device_added`` events produce an injection -- removals and other event
    types are ignored.
    """

    def __init__(
        self,
        inject_message: Callable[[str], None],
        socket_path: str = DEFAULT_EVENT_SOCKET,
    ) -> None:
        self.inject_message = inject_message
        self.socket_path = socket_path
        self._sock: socket.socket | None = None

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.socket_path)
        self._sock = sock

    def process_event(self, event: dict) -> bool:
        """Dispatch one event. Returns True iff a wizard was injected."""
        if event.get("type") != EVENT_DEVICE_ADDED:
            return False
        device = event.get("device")
        if not isinstance(device, dict):
            return False
        self.inject_message(build_wizard_prompt(device))
        return True

    def pump(self, reader: Iterable[bytes], max_events: int | None = None) -> int:
        """Consume events from `reader`, injecting on each device_added.

        Returns the number of wizards injected. `max_events` bounds the loop
        for tests and callers that want a single drain; None runs until the
        reader is exhausted (the socket closed).
        """
        injected = 0
        for event in iter_json_lines(reader):
            if self.process_event(event):
                injected += 1
                if max_events is not None and injected >= max_events:
                    break
        return injected

    def run(self, max_events: int | None = None) -> int:
        """Connect (if needed) and pump the live socket until it closes."""
        if self._sock is None:
            self.connect()
        assert self._sock is not None
        try:
            return self.pump(_socket_lines(self._sock), max_events=max_events)
        finally:
            self.close()

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
