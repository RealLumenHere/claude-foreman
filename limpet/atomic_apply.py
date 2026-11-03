"""Atomic config transaction wrapper (wq#4828, aerynOS MOSS pattern).

A fleet config change (a compose restart, a litellm_config.yaml swap, a
fleet-exec-policy.yaml edit, a systemd unit install) either fully applies or
fully reverts -- never a half-applied state (new config, old container)
left live. We used to do lumen_episode.py precheck + a manual
backup-before-change, but the rollback itself was manual and partial; this
is the automatic all-or-nothing wrapper for that step.

Sequence: snapshot (sha256 + copy) every file about to change into a
staging dir, apply every new file, run a post-apply health-check command.
Non-zero exit rolls every file back to its snapshot (or deletes it, if it
didn't exist before) and runs an optional rollback command to restart
affected services to the known-good config. Zero exit drops the staging
dir -- that's the commit.

Injectable runner/clock pattern follows health.py so this is testable
without a real subprocess or wall clock.
Ref: https://aerynos.com/blog/2025/03/29/aerynos-the-os-as-infrastructure/
"""
from __future__ import annotations

import dataclasses
import hashlib
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple


@dataclasses.dataclass(frozen=True)
class FileChange:
    path: Path
    content: bytes


@dataclasses.dataclass(frozen=True)
class TransactionResult:
    committed: bool
    reason: str
    checked_at: float
    snapshot_dir: Optional[str] = None


Runner = Callable[[str, int], Tuple[int, str]]


def _shell_runner(command: str, timeout: int):
    proc = subprocess.run(
        command, shell=True, timeout=timeout,
        capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def run_transaction(
    changes: Sequence[FileChange],
    health_check: str,
    *,
    rollback_cmd: Optional[str] = None,
    health_timeout: int = 30,
    staging_dir: Optional[str] = None,
    runner: Optional[Runner] = None,
    on_commit: Optional[Callable[[TransactionResult], None]] = None,
    now=None,
) -> TransactionResult:
    """Apply `changes` as one all-or-nothing transaction.

    on_commit, if given, runs only after a successful commit -- the hook
    point for recording a lumen_episode.py note; kept injectable rather
    than imported directly so this module has no hard dependency on
    memoryd/sqlite and stays unit-testable.
    """
    now = time.time() if now is None else now
    runner = _shell_runner if runner is None else runner
    stage = Path(staging_dir) if staging_dir else Path(tempfile.mkdtemp(prefix="atomic-apply-"))
    stage.mkdir(parents=True, exist_ok=True)

    # snapshot step: sha256 + copy every file that exists today, so a
    # rollback restores exact prior bytes; record "absent" for files the
    # transaction creates fresh, so rollback deletes rather than restores.
    snapshots = []
    for change in changes:
        target = Path(change.path)
        if target.exists():
            original = target.read_bytes()
            digest = hashlib.sha256(original).hexdigest()
            snap_path = stage / f"{digest}.snap"
            snap_path.write_bytes(original)
            snapshots.append((target, snap_path))
        else:
            snapshots.append((target, None))

    for change in changes:
        target = Path(change.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(change.content)

    try:
        code, output = runner(health_check, health_timeout)
    except subprocess.TimeoutExpired:
        code, output = 1, f"health check timed out after {health_timeout}s"
    except OSError as exc:
        code, output = 1, f"health check failed to run: {exc}"

    if code == 0:
        shutil.rmtree(stage, ignore_errors=True)
        result = TransactionResult(True, "health check passed", now)
        if on_commit is not None:
            on_commit(result)
        return result

    for target, snap_path in snapshots:
        if snap_path is not None:
            target.write_bytes(snap_path.read_bytes())
        else:
            target.unlink(missing_ok=True)

    if rollback_cmd:
        runner(rollback_cmd, health_timeout)

    tail = (output or "").strip().splitlines()[-1:] or [""]
    reason = (
        f"health check failed (exit {code}): {tail[0]}; "
        f"rolled back {len(snapshots)} file(s)"
    )
    return TransactionResult(False, reason, now, snapshot_dir=str(stage))
