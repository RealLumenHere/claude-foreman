"""wq#1806: the Claude backend. One `claude -p` subprocess per turn, whose env and
argv are fworker's OWN construction — imported, never copied or patched
(PHASE0_WIRING_SPEC.md A.4). This is a hard rule, not a convenience: the Messages API,
the Agent SDK, and LiteLLM-forwarding the OAuth token upstream are all explicitly
disallowed paths for that token (FOREMAN_RESEARCH.md §7 — the CLI subprocess is the
only sanctioned shape for subscription auth). `fworker.build_env` reads the token
itself, per spawn, via `fworker.read_oauth_token()` (never cached here, never read by
this module directly) — the same auth path 1306 fleet attempts already validated
(PHASE0_WIRING_SPEC.md A.1-A.3). `build_env` also branches local-pool models onto the
LiteLLM gateway env instead of the static bearer, so this one module serves both the
`claude-max` and `litellm-mechanical` harnesses — which backend runs is a property of
the requested model, not of which despag module built the command.
"""
from __future__ import annotations

import os
import sys

_FWORKER_DIR = "/home/lumen/foreman/harness"
if _FWORKER_DIR not in sys.path:
    sys.path.insert(0, _FWORKER_DIR)

import fworker  # noqa: E402 — path must be inserted first; this IS the import, never a copy.

CLAUDE_BIN = os.environ.get("DESPAG_CLAUDE_BIN", "claude")


def build_turn(*, worker_id: str, model: str, effort: str | None, worktree: str,
                ticket_id: str, prompt: str, add_dirs: list[str] | None = None,
                max_turns: int | None = None, resume_session: str | None = None,
                settings_file: str | None = None) -> tuple[list[str], dict]:
    """Return `(cmd, env)` for one `claude -p` turn — ready for
    `despag.runner.turn.Turn`/`subprocess.Popen`. Every argument maps straight onto
    `fworker.build_env`/`fworker.build_claude_cmd`'s own parameters; despag supplies
    UHP-shaped values (session workspace as `worktree`, the UHP response/session id as
    `ticket_id`) but never reimplements what those two functions decide."""
    env = fworker.build_env(worker_id, model, effort, worktree, ticket_id)
    cmd = fworker.build_claude_cmd(
        prompt, model, effort, settings_file or fworker.SETTINGS_FILE, add_dirs or [],
        max_turns=max_turns, resume_session=resume_session, claude_bin=CLAUDE_BIN,
    )
    return cmd, env
