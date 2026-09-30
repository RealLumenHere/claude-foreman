"""Auto-imported by CPython at interpreter startup when this directory sits
on PYTHONPATH (wq#3231). Wires up langfuse_debug_capture.install() so the
litellm container captures the real exception behind langfuse's generic
"Unexpected error occurred" line without editing litellm's own config or
raising the proxy's overall log_level.

Deploy: bind-mount this directory read-only into the litellm container and
prepend it to PYTHONPATH (e.g. PYTHONPATH=/opt/wq3231-debug:$PYTHONPATH in
the compose env). That mount/env edit is outside every --add-dir this BUILD
lane was granted (the litellm compose file lives under /home/lumen/docker,
per ops/wq3122-litellm-langfuse-trace-drop/README.md) and litellm cannot be
restarted from this lane (see maintenance-window.md) -- wiring it in is
queued as a follow-up for the next maintenance window
(2026-10-05 02:00-05:00 PT v1.96.2 recreate, wq#3115/wq#3221) rather than
gating this ticket on it.
"""

from langfuse_debug_capture import install

install()
