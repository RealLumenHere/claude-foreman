"""wq#4509 -- dynamic system-prompt builder for the LIMPET agent core.

design/wq3806_limpet_architecture.md §6 ("Agent core") describes the loop but names
no module for assembling what the model sees each turn; up to this ticket the system
prompt was a single static string. ``Planner.build_system_prompt`` replaces that with
three sections rebuilt fresh before every LLM call, so the model always reasons from
live state instead of a snapshot taken at session start:

  - the HAL device inventory (what is plugged in right now)
  - the active workspace's manifest.json (what target this session is investigating)
  - a "warm band" of the most recent tool-call evidence (what just happened)

Each section degrades to a plain status line instead of raising when its source is
absent (no HAL daemon running, no workspace open yet, no calls made yet) -- a planner
that crashes because the box has no USB devices attached is worse than one that says
so in the prompt.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workspace_report as wr  # noqa: E402
from workspace_console import format_line, project, read_blocks  # noqa: E402

BASE_SYSTEM_PROMPT = (
    "You are the LIMPET hardware reverse-engineering agent. You act on one workspace "
    "at a time: identify the target, choose a toolset for its class, orchestrate "
    "extract/static/emulate/verify steps through the tool registry, and write every "
    "result to the workspace as evidence (design/wq3806_limpet_architecture.md §6)."
)

# hal.sock (/run/limpet/hal.sock) is the udev notify daemon's input: a SOCK_DGRAM
# fire-and-forget channel with no query reply (limpet_hal_notify.py only ever sends).
# The only queryable snapshot of the live inventory is the file the daemon writes
# atomically on every change (ops/wq3809-limpet-hal/hal_daemon.py:_write_inventory).
# The socket's existence is used only as a liveness check for the daemon; the socket
# itself is never read.
HAL_SOCKET = os.environ.get("LIMPET_HAL_SOCKET", "/run/limpet/hal.sock")
HAL_INVENTORY_PATH = os.environ.get("LIMPET_INVENTORY", "/var/lib/limpet/hardware.json")

WARM_BAND_SIZE = 5


class Planner:
    """Builds the system prompt for one agent turn against one workspace."""

    def __init__(self, workspace: str | None = None, base_prompt: str = BASE_SYSTEM_PROMPT):
        self.workspace = workspace
        self.base_prompt = base_prompt

    def build_system_prompt(self) -> str:
        sections = [self.base_prompt.rstrip(), self._hal_inventory_section()]
        if self.workspace:
            sections.append(self._workspace_manifest_section())
            sections.append(self._warm_band_section())
        return "\n\n".join(s for s in sections if s)

    def _hal_inventory_section(self) -> str:
        devices = self._read_hal_inventory()
        if not devices:
            return (
                "## Attached hardware\n"
                "None detected (HAL daemon not running, or no devices attached)."
            )
        lines = ["## Attached hardware"]
        for d in devices:
            caps_raw = d.get("capabilities")
            caps = ", ".join(str(c) for c in caps_raw) if isinstance(caps_raw, list) else ""
            descriptor = wr.mask_text(str(d.get("descriptor") or ""))
            lines.append(
                f"- {d.get('name', '?')} ({d.get('hw_type', '?')}) "
                f"at {d.get('device_node', '?')} [{d.get('usb_id', '?')}] "
                f"status={d.get('status', '?')} capabilities=[{caps}] "
                f"descriptor={descriptor or '-'}"
            )
        return "\n".join(lines)

    def _read_hal_inventory(self) -> list[dict]:
        if not os.path.exists(HAL_SOCKET):
            return []
        try:
            with open(HAL_INVENTORY_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        # hal_daemon.py writes a fixed schema, so a non-dict entry means the file was
        # truncated/hand-edited: drop the bad entry rather than let d.get() raise.
        return [d for d in data if isinstance(d, dict)]

    def _workspace_manifest_section(self) -> str:
        path = os.path.join(self.workspace, "manifest.json")
        try:
            with open(path, encoding="utf-8") as fh:
                man = json.load(fh)
        except (OSError, ValueError):
            return "## Active workspace\nNo manifest.json -- not a Limpet workspace."
        if not isinstance(man, dict):
            return "## Active workspace\nmanifest.json is not a JSON object."
        # manifest.json is operator-editable, so every field is checked at its point of
        # use, the same guard workspace_report.py:313,315 and agent_tui.py:114 apply to
        # these two fields -- a wrong type degrades the line, it never raises.
        identity = man.get("identity") if isinstance(man.get("identity"), dict) else {}
        ident_str = ", ".join(f"{k}={v}" for k, v in identity.items()) or "-"
        hw_bound = man.get("hardware_bound") if isinstance(man.get("hardware_bound"), list) else []
        hw_str = "; ".join(
            str(h.get("adapter", "?")) if isinstance(h, dict) else "?" for h in hw_bound
        ) or "none"
        return (
            "## Active workspace\n"
            f"target_id={man.get('target_id', '?')} class={man.get('class', '?')} "
            f"identity=({ident_str}) hardware_bound=({hw_str})"
        )

    def _warm_band_section(self, limit: int = WARM_BAND_SIZE) -> str:
        try:
            blocks = read_blocks(self.workspace)
        except OSError:
            # read_blocks skips malformed lines but still opens the path; an unreadable
            # evidence log is the same "source absent" case as the two sections above.
            return "## Recent tool results (warm band)\nEvidence log unreadable."
        if not blocks:
            return "## Recent tool results (warm band)\nNone yet."
        lines = ["## Recent tool results (warm band)"]
        for block in blocks[-limit:]:
            lines.append("- " + format_line(project(block)))
        return "\n".join(lines)
