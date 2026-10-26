"""Tests for action_gate.py — TIERED_INTELLIGENCE_DESIGN.md Layers 1-3 (wq#985).
Test-to-failure: prove RED blocks, prove AMBER snapshots (and BLOCKS on snapshot
failure), prove the loop breaker trips (and does NOT trip on a healthy TDD loop),
prove project rules are additive, prove the hook fails closed."""
import json
import os
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import action_gate as ag
import read_dir_sweep as _rds

GATE = os.path.join(os.path.dirname(__file__), "..", "action_gate.py")


def run_hook(payload, env_extra=None):
    env = dict(os.environ)
    env.pop(ag.GATE_RULES_ENV, None)
    env.setdefault("FOREMAN_JUDGE_SHADOW", "0")  # wq#2797: hermetic by default, no live judge calls
    env.update(env_extra or {})
    p = subprocess.run([sys.executable, GATE, "hook"], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=60)
    return p.returncode, p.stderr


def bash_payload(cmd, cwd="/tmp"):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": cwd}


# ---------------- Layer 1: RED blocks, agent-visible message ----------------

RED_CASES = [
    "dd if=/dev/zero of=/dev/sda bs=4M",
    "mkfs.ext4 /dev/sdb1",
    "wipefs -a /dev/sdc",
    "fastboot erase userdata",
    "fastboot -w",
    "fastboot flash boot boot.img",
    "adb shell recovery --wipe_data",
    "adb shell am broadcast -a android.intent.action.FACTORY_RESET",
    "sqlite3 /home/lumen/minerva/minerva.db 'DROP TABLE archival_notes'",
    "psql -c 'TRUNCATE TABLE users'",
    "rm -rf /etc/nginx",
    "rm -rf /",
    "rm -rf /home/lumen/.ssh",
    "shred -u /home/lumen/.secrets/key.token",
    "echo x > /dev/sda",
    "cat img.bin > /dev/mmcblk0",
]


@pytest.mark.parametrize("cmd", RED_CASES)
def test_red_blocked(tmp_path, cmd):
    rc, err = run_hook(bash_payload(cmd), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 2, f"RED not blocked: {cmd}"
    assert "ACTION BLOCKED" in err


def test_red_writes_handoff_and_flag(tmp_path):
    rc, _ = run_hook(bash_payload("mkfs.ext4 /dev/sdz"), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 2
    handoffs = [f for f in os.listdir(tmp_path) if f.startswith("red-block-")]
    assert handoffs, "no handoff doc written on RED"
    assert os.path.exists(tmp_path / "fable-review-requested.json")


def test_red_evasion_whitespace(tmp_path):
    rc, _ = run_hook(bash_payload("mkfs.ext4\t/dev/sdz"), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 2


def test_rules_file_tamper_blocked(tmp_path):
    for payload in (
        bash_payload("sed -i 's/NEVER/ALWAYS/' /home/x/.fworker-rules.yaml"),
        {"tool_name": "Write", "tool_input": {"file_path": "/home/x/.fworker-rules.yaml",
                                               "content": "gutted"}, "cwd": "/tmp"},
        {"tool_name": "Edit", "tool_input": {"file_path": "/p/.fworker-rules.yaml"}, "cwd": "/tmp"},
    ):
        rc, err = run_hook(payload, {ag.HANDOFF_ENV: str(tmp_path),
                                     "FOREMAN_EVENTS_DIR": str(tmp_path)})
        assert rc == 2, f"rules tamper not blocked: {payload}"


# ---------------- wq#2797: shadow-mode judge wiring (observe-only) ----------

FAKE_JUDGE_OK = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, sys
    print(json.dumps({"tier": "High", "verdict": "deny", "conditions": [],
                       "rationale": "fake judge for test", "model": "fake-model",
                       "latency_s": 0.01}))
    sys.exit(1)
""")

# Mirrors bin/judge's resolve_model()/litellm_key() early-exit shape: writes only to
# stderr and exits 3 with EMPTY stdout (a misconfigured pin file or missing key --
# never reaches call_judge at all). Distinct from the FAIL_CLOSED shape below.
FAKE_JUDGE_MISCONFIGURED = textwrap.dedent("""\
    #!/usr/bin/env python3
    import sys
    sys.stderr.write("judge: no model configured.\\n")
    sys.exit(3)
""")

# Mirrors bin/judge's own fail_closed() shape: a real JSON body on stdout with
# verdict == "FAIL_CLOSED", exit 3 (timeout / unreachable model / unparseable reply).
FAKE_JUDGE_FAIL_CLOSED = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, sys
    print(json.dumps({"verdict": "FAIL_CLOSED", "reason": "model unreachable",
                       "model": "fake-model", "note": "no cloud fallback by design"}))
    sys.exit(3)
""")

FAKE_JUDGE_TOUCH = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    open(os.environ["FAKE_JUDGE_TOUCHFILE"], "w").close()
    print(json.dumps({"tier": "Low", "verdict": "allow", "conditions": [],
                       "rationale": "fake", "model": "fake", "latency_s": 0.01}))
    sys.exit(0)
""")


def _fake_judge(tmp_path, name, script):
    p = tmp_path / name
    p.write_text(script)
    p.chmod(0o755)
    return str(p)


def _events(tmp_path, worker):
    path = tmp_path / f"{worker}.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def test_shadow_judge_logs_without_changing_red_verdict(tmp_path):
    judge = _fake_judge(tmp_path, "fake_judge_ok.py", FAKE_JUDGE_OK)
    rc, err = run_hook(bash_payload("mkfs.ext4 /dev/sdz"), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        "FOREMAN_JUDGE_SHADOW": "1", "FOREMAN_JUDGE_BIN": judge,
        "FOREMAN_WORKER": "shadow-ok"})
    assert rc == 2  # RED verdict/exit code is untouched by the shadow call
    assert "ACTION BLOCKED" in err
    kinds = [e["event"] for e in _events(tmp_path, "shadow-ok")]
    assert kinds == ["gate_red_block", "judge_shadow"]
    shadow = _events(tmp_path, "shadow-ok")[1]
    assert shadow["judge_verdict"] == "deny"
    assert shadow["judge_tier"] == "High"
    assert shadow["gate_reason"] == "mkfs (filesystem create destroys contents)"


def test_shadow_judge_disabled_skips_the_call(tmp_path):
    touchfile = tmp_path / "touched"
    judge = _fake_judge(tmp_path, "fake_judge_touch.py", FAKE_JUDGE_TOUCH)
    rc, _ = run_hook(bash_payload("mkfs.ext4 /dev/sdz"), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        "FOREMAN_JUDGE_SHADOW": "0", "FOREMAN_JUDGE_BIN": judge,
        "FAKE_JUDGE_TOUCHFILE": str(touchfile), "FOREMAN_WORKER": "shadow-off"})
    assert rc == 2
    assert not touchfile.exists(), "judge binary was invoked despite FOREMAN_JUDGE_SHADOW=0"
    assert [e["event"] for e in _events(tmp_path, "shadow-off")] == ["gate_red_block"]


def test_shadow_judge_misconfigured_never_blocks_the_gate(tmp_path):
    judge = _fake_judge(tmp_path, "fake_judge_bad.py", FAKE_JUDGE_MISCONFIGURED)
    rc, err = run_hook(bash_payload("mkfs.ext4 /dev/sdz"), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        "FOREMAN_JUDGE_SHADOW": "1", "FOREMAN_JUDGE_BIN": judge,
        "FOREMAN_WORKER": "shadow-misconfig"})
    assert rc == 2  # a misconfigured judge must never change the gate's own verdict
    assert "ACTION BLOCKED" in err
    events = _events(tmp_path, "shadow-misconfig")
    assert [e["event"] for e in events] == ["gate_red_block", "judge_shadow"]
    assert events[1]["judge_verdict"] is None  # empty stdout parses to {}: no verdict key


def test_shadow_judge_fail_closed_is_logged_as_such(tmp_path):
    judge = _fake_judge(tmp_path, "fake_judge_failclosed.py", FAKE_JUDGE_FAIL_CLOSED)
    rc, err = run_hook(bash_payload("mkfs.ext4 /dev/sdz"), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        "FOREMAN_JUDGE_SHADOW": "1", "FOREMAN_JUDGE_BIN": judge,
        "FOREMAN_WORKER": "shadow-failclosed"})
    assert rc == 2  # still untouched -- FAIL_CLOSED from the judge is itself a shadow signal
    assert "ACTION BLOCKED" in err
    events = _events(tmp_path, "shadow-failclosed")
    assert [e["event"] for e in events] == ["gate_red_block", "judge_shadow"]
    assert events[1]["judge_verdict"] == "FAIL_CLOSED"


def test_shadow_judge_missing_binary_logs_error_not_exception(tmp_path):
    rc, err = run_hook(bash_payload("mkfs.ext4 /dev/sdz"), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        "FOREMAN_JUDGE_SHADOW": "1", "FOREMAN_JUDGE_BIN": str(tmp_path / "no-such-binary"),
        "FOREMAN_WORKER": "shadow-missing"})
    assert rc == 2  # a broken/absent judge binary must never crash or change the gate
    assert "ACTION BLOCKED" in err
    kinds = [e["event"] for e in _events(tmp_path, "shadow-missing")]
    assert kinds == ["gate_red_block", "judge_shadow_error"]


def test_shadow_judge_never_fires_on_green_or_amber(tmp_path):
    touchfile = tmp_path / "touched"
    judge = _fake_judge(tmp_path, "fake_judge_touch2.py", FAKE_JUDGE_TOUCH)
    base = {ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
            "FOREMAN_JUDGE_SHADOW": "1", "FOREMAN_JUDGE_BIN": judge,
            "FAKE_JUDGE_TOUCHFILE": str(touchfile)}
    rc, _ = run_hook(bash_payload("ls -la /tmp"), base)
    assert rc == 0
    assert not touchfile.exists(), "judge invoked on a GREEN command"
    target = tmp_path / "config.yaml"
    target.write_text("x: 1\n")
    rc, _ = run_hook({"tool_name": "Edit", "tool_input": {"file_path": str(target)},
                      "cwd": str(tmp_path)},
                     {**base, ag.SNAPDIR_ENV: str(tmp_path / "snaps")})
    assert rc == 0
    assert not touchfile.exists(), "judge invoked on an AMBER command"


# ---------------- wq#2798: live enforcement cutover (Layer 4) ---------------

FAKE_JUDGE_ALLOW = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, sys
    print(json.dumps({"tier": "Low", "verdict": "allow", "conditions": [],
                       "rationale": "fake allow", "model": "fake-model", "latency_s": 0.01}))
    sys.exit(0)
""")

FAKE_JUDGE_ALLOW_WITH_CONDITIONS = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, sys
    print(json.dumps({"tier": "Medium", "verdict": "allow-with-conditions",
                       "conditions": ["snapshot before executing", "notify Frank"],
                       "rationale": "fake conditional", "model": "fake-model",
                       "latency_s": 0.01}))
    sys.exit(2)
""")


def test_judge_enforce_off_by_default_still_hard_blocks_even_if_judge_would_allow(tmp_path):
    """The kill switch: FOREMAN_JUDGE_ENFORCE unset must leave today's behavior
    untouched even when a live judge is configured and would allow the action --
    wq#2801 (Frank's tier-policy sign-off) has not landed, so enforcement must
    default to inert."""
    judge = _fake_judge(tmp_path, "fake_judge_allow.py", FAKE_JUDGE_ALLOW)
    rc, err = run_hook(bash_payload("mkfs.ext4 /dev/sdz"), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        "FOREMAN_JUDGE_BIN": judge, "FOREMAN_WORKER": "enforce-off"})
    assert rc == 2, "RED must still hard-block when FOREMAN_JUDGE_ENFORCE is unset"
    assert "ACTION BLOCKED" in err
    assert [e["event"] for e in _events(tmp_path, "enforce-off")] == ["gate_red_block"]


def test_judge_enforce_allow_lets_the_action_through(tmp_path):
    judge = _fake_judge(tmp_path, "fake_judge_allow.py", FAKE_JUDGE_ALLOW)
    rc, err = run_hook(bash_payload("mkfs.ext4 /dev/sdz"), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        "FOREMAN_JUDGE_ENFORCE": "1", "FOREMAN_JUDGE_BIN": judge,
        "FOREMAN_WORKER": "enforce-allow"})
    assert rc == 0, f"judge allow must let the action through: {err}"
    kinds = [e["event"] for e in _events(tmp_path, "enforce-allow")]
    assert kinds == ["gate_red_block", "judge_enforce", "gate_judge_allow"]


def test_judge_enforce_deny_still_hard_blocks(tmp_path):
    judge = _fake_judge(tmp_path, "fake_judge_deny.py", FAKE_JUDGE_OK)  # verdict: deny
    rc, err = run_hook(bash_payload("mkfs.ext4 /dev/sdz"), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        "FOREMAN_JUDGE_ENFORCE": "1", "FOREMAN_JUDGE_BIN": judge,
        "FOREMAN_WORKER": "enforce-deny"})
    assert rc == 2
    assert "ACTION BLOCKED" in err
    kinds = [e["event"] for e in _events(tmp_path, "enforce-deny")]
    assert kinds == ["gate_red_block", "judge_enforce", "gate_judge_deny"]


def test_judge_enforce_fail_closed_still_hard_blocks(tmp_path):
    """Fail-closed preserved, no exceptions: judge FAIL_CLOSED = today's hard
    RED block, unconditionally."""
    judge = _fake_judge(tmp_path, "fake_judge_failclosed.py", FAKE_JUDGE_FAIL_CLOSED)
    rc, err = run_hook(bash_payload("mkfs.ext4 /dev/sdz"), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        "FOREMAN_JUDGE_ENFORCE": "1", "FOREMAN_JUDGE_BIN": judge,
        "FOREMAN_WORKER": "enforce-failclosed"})
    assert rc == 2
    assert "ACTION BLOCKED" in err
    kinds = [e["event"] for e in _events(tmp_path, "enforce-failclosed")]
    assert kinds == ["gate_red_block", "judge_enforce_fail_closed", "gate_judge_deny"]


def test_judge_enforce_missing_binary_fails_closed(tmp_path):
    rc, err = run_hook(bash_payload("mkfs.ext4 /dev/sdz"), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        "FOREMAN_JUDGE_ENFORCE": "1", "FOREMAN_JUDGE_BIN": str(tmp_path / "no-such-binary"),
        "FOREMAN_WORKER": "enforce-missing"})
    assert rc == 2
    assert "ACTION BLOCKED" in err
    kinds = [e["event"] for e in _events(tmp_path, "enforce-missing")]
    assert kinds == ["gate_red_block", "judge_enforce_fail_closed", "gate_judge_deny"]


def test_judge_enforce_allow_with_conditions_stages_snapshot_before_allowing(tmp_path):
    target = tmp_path / "secret.token"
    target.write_text("pre-image content\n")
    judge = _fake_judge(tmp_path, "fake_judge_cond.py", FAKE_JUDGE_ALLOW_WITH_CONDITIONS)
    rc, err = run_hook(bash_payload(f"shred -u {target}"), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        ag.SNAPDIR_ENV: str(tmp_path / "snaps"),
        "FOREMAN_JUDGE_ENFORCE": "1", "FOREMAN_JUDGE_BIN": judge,
        "FOREMAN_WORKER": "enforce-conditions"})
    assert rc == 0, f"allow-with-conditions must let the action through once staged: {err}"
    events = _events(tmp_path, "enforce-conditions")
    kinds = [e["event"] for e in events]
    assert kinds == ["gate_red_block", "judge_enforce", "gate_judge_allow_with_conditions"]
    staged = events[-1]["staged"]
    conditions = [s["condition"] for s in staged]
    assert "snapshot before executing" in conditions
    assert "notify Frank" in conditions
    snap_entry = next(s for s in staged if s["condition"] == "snapshot before executing")
    assert snap_entry["artifacts"], "snapshot condition produced no pre-image artifact"


def test_judge_enforce_condition_staging_failure_fails_closed(tmp_path):
    """The AMBER invariant applies to Layer 4 too: a condition action_gate
    cannot actually stage on disk must fail closed, never silently pass
    through. Forced here by pointing the snapshot at a cwd that does not
    exist, so the git-fallback pre-image (no extractable file target in the
    command) cannot run."""
    judge = _fake_judge(tmp_path, "fake_judge_cond.py", FAKE_JUDGE_ALLOW_WITH_CONDITIONS)
    missing_cwd = str(tmp_path / "does-not-exist")
    rc, err = run_hook(bash_payload("wipefs -a /dev/sdc", cwd=missing_cwd), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        ag.SNAPDIR_ENV: str(tmp_path / "snaps"),
        "FOREMAN_JUDGE_ENFORCE": "1", "FOREMAN_JUDGE_BIN": judge,
        "FOREMAN_WORKER": "enforce-stage-fail"})
    assert rc == 2, "a failed condition staging must fail closed, not allow through"
    assert "ACTION BLOCKED" in err
    kinds = [e["event"] for e in _events(tmp_path, "enforce-stage-fail")]
    assert kinds == ["gate_red_block", "judge_enforce", "judge_enforce_condition_stage_failed",
                      "gate_judge_deny"]


# ---------------- Layer 1: GREEN passes ----------------

GREEN_CASES = [
    "ls -la /home/lumen",
    "git commit -m 'feat: thing'",
    "git push origin feat/x",
    "grep -rn pattern src/",
    "python3 -m pytest tests/ -x",
    "rm -rf /tmp/scratch/build",
    "fdisk -l",
]


@pytest.mark.parametrize("cmd", GREEN_CASES)
def test_green_passes(cmd):
    rc, err = run_hook(bash_payload(cmd))
    assert rc == 0, f"GREEN blocked: {cmd} :: {err}"


# ---------------- Layer 1: AMBER snapshots, fail-closed on snapshot error ----

def test_amber_edit_snapshots_preimage(tmp_path):
    target = tmp_path / "config.yaml"
    target.write_text("original: true\n")
    snapdir = tmp_path / "snaps"
    rc, _ = run_hook({"tool_name": "Edit",
                      "tool_input": {"file_path": str(target)}, "cwd": str(tmp_path)},
                     {ag.SNAPDIR_ENV: str(snapdir), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 0
    snaps = list(snapdir.iterdir())
    assert snaps, "no pre-image snapshot for Edit"
    assert snaps[0].read_text() == "original: true\n"


def test_amber_sed_snapshots_target(tmp_path):
    target = tmp_path / "app.conf"
    target.write_text("k=v\n")
    snapdir = tmp_path / "snaps"
    rc, _ = run_hook(bash_payload(f"sed -i 's/v/w/' {target}", cwd=str(tmp_path)),
                     {ag.SNAPDIR_ENV: str(snapdir), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 0
    assert any(s.read_text() == "k=v\n" for s in snapdir.iterdir())


def test_amber_write_new_file_is_green(tmp_path):
    rc, _ = run_hook({"tool_name": "Write",
                      "tool_input": {"file_path": str(tmp_path / "brand-new.txt")},
                      "cwd": str(tmp_path)})
    assert rc == 0


def test_amber_snapshot_failure_blocks(tmp_path, monkeypatch):
    """Invariant: AMBER never executes unsnapshotted. Point the snapdir at an
    unwritable location and prove the command is BLOCKED, not waved through."""
    target = tmp_path / "f.conf"
    target.write_text("x\n")
    rc, err = run_hook({"tool_name": "Edit", "tool_input": {"file_path": str(target)},
                        "cwd": str(tmp_path)},
                       {ag.SNAPDIR_ENV: "/proc/definitely-not-writable/snaps",
                        "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 2
    assert "BLOCKED" in err


# ---------------- wq#2116: credential pre-images stay out of the snapdir ----

FAKE_PAT = "ghp_" + "Zq7" + "x" * 33          # gh_pat shape, not a real token


def _amber_edit(tmp_path, target, snapdir, vault):
    return run_hook({"tool_name": "Edit", "tool_input": {"file_path": str(target)},
                     "cwd": str(tmp_path)},
                    {ag.SNAPDIR_ENV: str(snapdir), "FOREMAN_EVENTS_DIR": str(tmp_path),
                     "FOREMAN_GATE_SNAP_VAULT": str(vault)})


def test_credential_preimage_goes_to_vault_not_snapdir(tmp_path):
    """The snapdir sits inside foreman, which is every worker's --add-dir. A .env
    pre-image must leave a pointer there and the bytes somewhere else."""
    target = tmp_path / "agent.env"
    target.write_text(f"GITHUB_TOKEN={FAKE_PAT}\n")
    snapdir, vault = tmp_path / "snaps", tmp_path / "vault"
    rc, _ = _amber_edit(tmp_path, target, snapdir, vault)
    assert rc == 0
    snaps = list(snapdir.iterdir())
    assert [s.name.endswith(".held-out") for s in snaps] == [True], snaps
    assert FAKE_PAT not in snaps[0].read_text()
    stowed = list(vault.iterdir())
    assert [s.read_text() for s in stowed] == [f"GITHUB_TOKEN={FAKE_PAT}\n"]
    assert oct(stowed[0].stat().st_mode)[-3:] == "600"


def test_credential_basename_alone_diverts(tmp_path):
    """`.env` is a credential by convention — no token in the body required."""
    target = tmp_path / "prod.env"
    target.write_text("FEATURE_FLAG=1\n")
    snapdir, vault = tmp_path / "snaps", tmp_path / "vault"
    rc, _ = _amber_edit(tmp_path, target, snapdir, vault)
    assert rc == 0
    assert [p.name.endswith(".held-out") for p in snapdir.iterdir()] == [True]
    assert [p.read_text() for p in vault.iterdir()] == ["FEATURE_FLAG=1\n"]


def _register_stub(tmp_path, body):
    stub = tmp_path / "register-stub"
    stub.write_text("#!/bin/sh\n" + body)
    stub.chmod(0o755)
    return stub


def test_credential_preimage_is_registered_with_an_expiry(tmp_path):
    """wq#4599: the vault pre-image must carry a 7d expiry so it cannot sit there for 50 days."""
    target = tmp_path / "agent.env"
    target.write_text(f"GITHUB_TOKEN={FAKE_PAT}\n")
    log = tmp_path / "reg.log"
    stub = _register_stub(tmp_path, f'echo "$@" >> {log}\n')
    snapdir, vault = tmp_path / "snaps", tmp_path / "vault"
    rc, _ = run_hook({"tool_name": "Edit", "tool_input": {"file_path": str(target)}, "cwd": str(tmp_path)},
                     {ag.SNAPDIR_ENV: str(snapdir), "FOREMAN_EVENTS_DIR": str(tmp_path),
                      "FOREMAN_GATE_SNAP_VAULT": str(vault), "FOREMAN_BACKUP_REGISTER": str(stub)})
    assert rc == 0
    stowed = list(vault.iterdir())
    line = log.read_text().strip()
    assert line.startswith("register " + str(stowed[0])) and "--ttl 7d" in line
    assert FAKE_PAT not in line


def test_registration_failure_never_blocks_the_gate(tmp_path):
    target = tmp_path / "agent.env"
    target.write_text(f"GITHUB_TOKEN={FAKE_PAT}\n")
    stub = _register_stub(tmp_path, "exit 9\n")
    snapdir, vault = tmp_path / "snaps", tmp_path / "vault"
    rc, _ = run_hook({"tool_name": "Edit", "tool_input": {"file_path": str(target)}, "cwd": str(tmp_path)},
                     {ag.SNAPDIR_ENV: str(snapdir), "FOREMAN_EVENTS_DIR": str(tmp_path),
                      "FOREMAN_GATE_SNAP_VAULT": str(vault), "FOREMAN_BACKUP_REGISTER": str(stub)})
    assert rc == 0 and len(list(vault.iterdir())) == 1


def test_clean_preimage_is_not_registered(tmp_path):
    target = tmp_path / "config.yaml"
    target.write_text("original: true\n")
    log = tmp_path / "reg.log"
    stub = _register_stub(tmp_path, f'echo "$@" >> {log}\n')
    rc, _ = run_hook({"tool_name": "Edit", "tool_input": {"file_path": str(target)}, "cwd": str(tmp_path)},
                     {ag.SNAPDIR_ENV: str(tmp_path / "snaps"), "FOREMAN_EVENTS_DIR": str(tmp_path),
                      "FOREMAN_GATE_SNAP_VAULT": str(tmp_path / "vault"), "FOREMAN_BACKUP_REGISTER": str(stub)})
    assert rc == 0 and not log.exists()


def test_clean_preimage_still_lands_in_snapdir(tmp_path):
    """No divert for ordinary files: rollback keeps working where it always did."""
    target = tmp_path / "config.yaml"
    target.write_text("original: true\n")
    snapdir, vault = tmp_path / "snaps", tmp_path / "vault"
    rc, _ = _amber_edit(tmp_path, target, snapdir, vault)
    assert rc == 0
    assert [s.read_text() for s in snapdir.iterdir()] == ["original: true\n"]
    assert not vault.exists()


def test_symlink_preimage_diverts(tmp_path):
    """read_dir_sweep skips symlinks, so nothing ever scans the bytes copy2 follows
    through to. Innocuous link name, innocuous target name, real token (wq#2123)."""
    body = tmp_path / "body.txt"
    body.write_text(f"GITHUB_TOKEN={FAKE_PAT}\n")
    target = tmp_path / "notes.txt"
    target.symlink_to(body)
    snapdir, vault = tmp_path / "snaps", tmp_path / "vault"
    rc, _ = _amber_edit(tmp_path, target, snapdir, vault)
    assert rc == 0
    assert [p.name.endswith(".held-out") for p in snapdir.iterdir()] == [True]
    assert [p.read_text() for p in vault.iterdir()] == [f"GITHUB_TOKEN={FAKE_PAT}\n"]


def test_binary_preimage_diverts(tmp_path):
    """A NUL in the first 4KB stops the sweep's reader — a sqlite db or any binary
    carrying a token reads as clean text and must not land in the snapdir."""
    target = tmp_path / "blob.bin"
    target.write_bytes(b"SQLite format 3\0\0" + f"GITHUB_TOKEN={FAKE_PAT}\n".encode())
    snapdir, vault = tmp_path / "snaps", tmp_path / "vault"
    rc, _ = _amber_edit(tmp_path, target, snapdir, vault)
    assert rc == 0
    assert [p.name.endswith(".held-out") for p in snapdir.iterdir()] == [True]
    assert FAKE_PAT.encode() in list(vault.iterdir())[0].read_bytes()


def test_oversize_preimage_diverts(tmp_path):
    """Past MAX_FILE_BYTES the sweep reads nothing, so a token at the head of a big
    file is unscanned, not absent."""
    target = tmp_path / "big.log"
    target.write_text(f"GITHUB_TOKEN={FAKE_PAT}\n" + "x" * _rds.MAX_FILE_BYTES)
    snapdir, vault = tmp_path / "snaps", tmp_path / "vault"
    rc, _ = _amber_edit(tmp_path, target, snapdir, vault)
    assert rc == 0
    assert [p.name.endswith(".held-out") for p in snapdir.iterdir()] == [True]
    assert FAKE_PAT in list(vault.iterdir())[0].read_text()


def test_empty_preimage_still_lands_in_snapdir(tmp_path):
    """The boundary the divert must not cross: an empty file IS readable as text, so
    `_read_text` returning "" stays clean while returning None does not."""
    target = tmp_path / "empty.txt"
    target.write_text("")
    snapdir, vault = tmp_path / "snaps", tmp_path / "vault"
    rc, _ = _amber_edit(tmp_path, target, snapdir, vault)
    assert rc == 0
    assert [p.read_text() for p in snapdir.iterdir()] == [""]
    assert not vault.exists()


def test_is_sensitive_globs_the_symlink_target_name(tmp_path):
    """`.env` is a credential by convention; pointing a plain name at one does not
    launder it."""
    real = tmp_path / "prod.env"
    real.write_text("FEATURE_FLAG=1\n")
    link = tmp_path / "config.txt"
    link.symlink_to(real)
    assert ag._is_sensitive(path=str(link))


def test_is_sensitive_fails_closed_without_the_sweep(monkeypatch):
    monkeypatch.setattr(ag, "_rds", None)
    assert ag._is_sensitive(text="nothing interesting here")


# ---------------- Layer 3: project rules additive ----------------

MIKO_RULES = textwrap.dedent("""\
    project: miko3
    hardware_class: unrecoverable
    rules:
      - NEVER factory reset, wipe data, or erase partitions
      - NEVER flash without a verified full dump on disk
      - ALWAYS escalate to Fable before any write to the device
      - backup_required_before: [flash, dd, fastboot, "adb push /system"]
      - phase_gates: [research, planning, implementation]
    red_patterns:
      - pattern: '\\bmiko-tool\\b.*--reset'
        reason: miko-tool reset is device-destructive
    context:
      - The account link lives on Miko's servers, not the device
      - A local factory reset orphans the device from the server
""")


def compiled(tmp_path, yaml_text=MIKO_RULES):
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    (proj / ".fworker-rules.yaml").write_text(yaml_text)
    out = str(tmp_path / "compiled.json")
    return ag.compile_rules([str(proj)], out)


def test_compile_rules_shape(tmp_path):
    out = compiled(tmp_path)
    data = json.load(open(out))
    assert data["project"] == "miko3"
    assert data["hardware_class"] == "unrecoverable"
    assert "flash" in data["backup_required_before"]
    assert len(data["red_patterns"]) == 1
    assert len(data["prose_rules"]) == 3


def test_compile_rules_none_when_absent(tmp_path):
    assert ag.compile_rules([str(tmp_path)], str(tmp_path / "o.json")) is None


def test_compile_rules_malformed_raises(tmp_path):
    proj = tmp_path / "p2"
    proj.mkdir()
    (proj / ".fworker-rules.yaml").write_text("red_patterns:\n  - pattern: '['\n")
    with pytest.raises(Exception):
        ag.compile_rules([str(proj)], str(tmp_path / "o.json"))


def test_project_red_pattern_enforced(tmp_path):
    out = compiled(tmp_path)
    rc, err = run_hook(bash_payload("miko-tool --reset --confirm"),
                       {ag.GATE_RULES_ENV: out, ag.HANDOFF_ENV: str(tmp_path),
                        "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 2
    assert "hardware_class=unrecoverable" in err


def test_backup_required_token_blocks_without_backup(tmp_path):
    out = compiled(tmp_path)
    rc, err = run_hook(bash_payload("adb push /system/framework.jar /system/"),
                       {ag.GATE_RULES_ENV: out, ag.HANDOFF_ENV: str(tmp_path),
                        "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 2


def test_backup_required_token_ambers_with_local_backup(tmp_path):
    dump = tmp_path / "full_flash.bin"
    dump.write_bytes(b"\x00" * 1024)
    yaml_text = MIKO_RULES + f"backups:\n  - {dump}\n"
    out = compiled(tmp_path, yaml_text)
    # wq#1006: research gate now precedes the flash/backup path — satisfy it so
    # this test keeps proving the backup->AMBER downgrade in isolation.
    research = tmp_path / "proj" / "research"
    research.mkdir(parents=True, exist_ok=True)
    (research / "PRIOR_ART.md").write_text("## finding\nprior art on file\n")
    snapdir = tmp_path / "snaps"
    rc, _ = run_hook(bash_payload("fastboot flash boot boot.img"),
                     {ag.GATE_RULES_ENV: out, ag.SNAPDIR_ENV: str(snapdir),
                      "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 0, "verified local backup should downgrade flash to AMBER"


def test_builtins_never_weakened_by_project_rules(tmp_path):
    """Additive-only guarantee: a rules file cannot un-RED a builtin."""
    out = compiled(tmp_path, "project: sneaky\nred_patterns: []\namber_patterns: []\n")
    rc, _ = run_hook(bash_payload("mkfs.ext4 /dev/sda1"),
                     {ag.GATE_RULES_ENV: out, ag.HANDOFF_ENV: str(tmp_path),
                      "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 2


# ---------------- wq#1006: research phase gate ----------------
# Distinct project name so a real /home/lumen/projects/<name>/research/
# PRIOR_ART.md can never satisfy these tests' gate from outside the tmp tree.

GATEPROJ_RULES = textwrap.dedent("""\
    project: wq1006-gatetest
    hardware_class: unrecoverable
    rules:
      - phase_gates: [research, planning, implementation]
""")


def _env(tmp_path, out):
    return {ag.GATE_RULES_ENV: out, ag.HANDOFF_ENV: str(tmp_path),
            "FOREMAN_EVENTS_DIR": str(tmp_path)}


def test_research_gate_blocks_adb_without_prior_art(tmp_path):
    out = compiled(tmp_path, GATEPROJ_RULES)
    rc, err = run_hook(bash_payload("adb shell getprop ro.build.fingerprint"),
                       _env(tmp_path, out))
    assert rc == 2
    assert "research phase gate" in err and "fleet-search" in err


def test_research_gate_blocks_flash_even_with_verified_backup(tmp_path):
    """Research trumps backup: a local dump alone does not license device work."""
    dump = tmp_path / "dump.bin"
    dump.write_bytes(b"\x00" * 512)
    out = compiled(tmp_path, GATEPROJ_RULES + f"backups:\n  - {dump}\n")
    rc, err = run_hook(bash_payload("fastboot flash boot boot.img"),
                       _env(tmp_path, out))
    assert rc == 2
    assert "research phase gate" in err


def test_research_gate_opens_with_nonempty_prior_art(tmp_path):
    out = compiled(tmp_path, GATEPROJ_RULES)
    research = tmp_path / "proj" / "research"
    research.mkdir(parents=True)
    (research / "PRIOR_ART.md").write_text("## web: teardown thread found\n")
    rc, err = run_hook(bash_payload("adb devices"), _env(tmp_path, out))
    assert rc == 0, f"gate should open on non-empty PRIOR_ART: {err}"


def test_research_gate_empty_prior_art_still_blocks(tmp_path):
    out = compiled(tmp_path, GATEPROJ_RULES)
    research = tmp_path / "proj" / "research"
    research.mkdir(parents=True)
    (research / "PRIOR_ART.md").write_text("")
    rc, _ = run_hook(bash_payload("adb devices"), _env(tmp_path, out))
    assert rc == 2


def test_research_gate_fleet_search_itself_exempt(tmp_path):
    """The satisfaction path must not deadlock on device tokens in its query."""
    out = compiled(tmp_path, GATEPROJ_RULES)
    rc, err = run_hook(bash_payload(
        "fleet-search --project wq1006-gatetest web 'miko3 adb root jailbreak'"),
        _env(tmp_path, out))
    assert rc == 0, f"standalone fleet-search must pass: {err}"


def test_research_gate_fleet_search_chain_not_exempt(tmp_path):
    out = compiled(tmp_path, GATEPROJ_RULES)
    rc, _ = run_hook(bash_payload(
        "fleet-search --project wq1006-gatetest web 'q'; adb shell ls /data"),
        _env(tmp_path, out))
    assert rc == 2, "chained command must not ride the fleet-search exemption"


def test_research_gate_prior_art_tamper_blocked(tmp_path):
    out = compiled(tmp_path, GATEPROJ_RULES)
    research = tmp_path / "proj" / "research"
    research.mkdir(parents=True)
    pa = research / "PRIOR_ART.md"
    for payload in (
        bash_payload(f"echo seeded > {pa}"),
        bash_payload(f"touch {pa}"),
        {"tool_name": "Write", "tool_input": {"file_path": str(pa),
                                               "content": "fake"}, "cwd": "/tmp"},
        {"tool_name": "Edit", "tool_input": {"file_path": str(pa)}, "cwd": "/tmp"},
    ):
        rc, err = run_hook(payload, _env(tmp_path, out))
        assert rc == 2, f"PRIOR_ART tamper not blocked: {payload}"
        assert "tamper" in err


def test_research_gate_needs_hardware_class(tmp_path):
    """phase_gates without hardware_class = software project; no device gate."""
    out = compiled(tmp_path, "project: softproj\nrules:\n"
                             "  - phase_gates: [research, implementation]\n")
    rc, err = run_hook(bash_payload("adb devices"), _env(tmp_path, out))
    assert rc == 0, err


def test_research_gate_needs_research_phase_declared(tmp_path):
    out = compiled(tmp_path, "project: hwproj\nhardware_class: recoverable\n"
                             "rules:\n  - phase_gates: [planning, implementation]\n")
    rc, err = run_hook(bash_payload("adb devices"), _env(tmp_path, out))
    assert rc == 0, err


def test_compile_rules_prior_art_paths(tmp_path):
    out = compiled(tmp_path, GATEPROJ_RULES)
    data = json.load(open(out))
    assert data["phase_gates"] == ["research", "planning", "implementation"]
    assert data["prior_art_paths"][0] == \
        "/home/lumen/projects/wq1006-gatetest/research/PRIOR_ART.md"
    assert any(p.startswith(str(tmp_path)) for p in data["prior_art_paths"])


# ---------------- hook fail-closed ----------------

def test_hook_fails_closed_on_garbage():
    p = subprocess.run([sys.executable, GATE, "hook"], input="not json {",
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 2


def test_hook_fails_closed_on_empty_command():
    rc, _ = run_hook({"tool_name": "Bash", "tool_input": {}, "cwd": "/tmp"})
    assert rc == 2


# ---------------- Layer 2: loop breaker ----------------

def tu(tool, tid, **inp):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": tool, "input": inp}]}}


def tr(tid, error=False, text="boom: thing failed"):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": error,
         "content": [{"type": "text", "text": text}]}]}}


def test_loop_same_class_failure_trips():
    t = ag.LoopTracker(threshold=3)
    trip = None
    for i in range(3):
        assert t.observe(tu("Bash", f"a{i}", command="fastboot devices")) is None
        trip = t.observe(tr(f"a{i}", error=True, text=f"error variant {i}: no device"))
    assert trip and "same command class" in trip


def test_loop_same_error_trips_across_classes():
    t = ag.LoopTracker(threshold=3)
    trip = None
    for i, cmd in enumerate(["adb devices", "adb shell ls", "adb wait-for-device"]):
        t.observe(tu("Bash", f"b{i}", command=cmd))
        trip = t.observe(tr(f"b{i}", error=True, text="adb: no devices/emulators found"))
    assert trip and "same error" in trip


# ---------------- wq#1107: permission refusals are a HARNESS trip, not a model loop ----

def test_permission_denial_detector_covers_observed_shapes():
    for text in ("This command requires approval",
                 "This Bash command contains multiple operations. The following parts "
                 "require approval: ps -p 4242 -o pid,etime,cmd",
                 "ls in '/home/lumen/.claude/' was blocked. For security, Claude Code "
                 "may only list files in the allowed working directories for this session"):
        assert ag.is_permission_denial(text), text
    # a real tool error must never be mistaken for one
    for text in ("adb: no devices/emulators found", "=== 3 failed, 1 passed ===", ""):
        assert not ag.is_permission_denial(text), text


def test_same_error_permission_trip_is_marked_permission():
    t = ag.LoopTracker(threshold=3)
    trip = None
    for i, cmd in enumerate(["ps -p 1 -o pid,cmd", "ps aux", "ps -ef"]):
        t.observe(tu("Bash", f"p{i}", command=cmd))
        trip = t.observe(tr(f"p{i}", error=True, text="This command requires approval"))
    assert trip and t.trip_kind == "PERMISSION"
    assert "HARNESS DEFECT" in t.report()


def test_same_class_permission_trip_is_marked_permission():
    """The compound-denial message embeds the command, so three retries of one shape
    produce three DIFFERENT error signatures — only the same-command-class counter
    trips. It still has to read as PERMISSION."""
    t = ag.LoopTracker(threshold=3)
    trip = None
    for i in range(3):
        t.observe(tu("Bash", f"c{i}", command=f"gh pr view {i} --json state"))
        trip = t.observe(tr(f"c{i}", error=True,
                            text=("This Bash command contains multiple operations. The "
                                  f"following parts require approval: gh pr view {i} "
                                  "--json state")))
    assert trip and "same command class" in trip
    assert t.trip_kind == "PERMISSION"


def test_ordinary_loop_is_not_marked_permission():
    t = ag.LoopTracker(threshold=3)
    for i in range(3):
        t.observe(tu("Bash", f"n{i}", command="adb devices"))
        t.observe(tr(f"n{i}", error=True, text="adb: no devices/emulators found"))
    assert t.tripped and t.trip_kind is None
    assert "HARNESS DEFECT" not in t.report()


def test_permission_streak_resets_on_successful_mutation():
    """Two refusals, then a successful Bash, then a third refusal of the same class is
    NOT a loop — the successful call is progress, same rule fail_counts already uses."""
    t = ag.LoopTracker(threshold=3)
    for i in range(2):
        t.observe(tu("Bash", f"r{i}", command="ps aux"))
        t.observe(tr(f"r{i}", error=True, text=f"parts require approval: ps aux #{i}"))
    t.observe(tu("Bash", "ok", command="git status"))
    assert t.observe(tr("ok", error=False, text="clean")) is None
    assert t.perm_counts == {}
    t.observe(tu("Bash", "r2", command="ps aux"))
    assert t.observe(tr("r2", error=True, text="parts require approval: ps aux #2")) is None


def test_healthy_tdd_loop_does_not_trip():
    """fail test -> edit (success) -> fail test -> edit -> fail test: the
    intervening successful mutations mean this is progress, not a loop."""
    t = ag.LoopTracker(threshold=3)
    for i in range(4):
        t.observe(tu("Bash", f"t{i}", command="python3 -m pytest tests/"))
        r = t.observe(tr(f"t{i}", error=True, text=f"=== {4-i} failed, {i} passed ==="))
        assert r is None, f"tripped on healthy TDD iteration {i}: {r}"
        t.observe(tu("Edit", f"e{i}", file_path="/x/src/mod.py"))
        assert t.observe(tr(f"e{i}", error=False, text="ok")) is None


def test_loop_reread_same_file_trips():
    t = ag.LoopTracker(threshold=3)
    trip = None
    for i in range(3):
        t.observe(tu("Read", f"r{i}", file_path="/x/README.md"))
        trip = t.observe(tr(f"r{i}", error=False, text="contents"))
    assert trip and "re-read" in trip


def test_reread_reset_by_mutation():
    t = ag.LoopTracker(threshold=3)
    for i in range(2):
        t.observe(tu("Read", f"r{i}", file_path="/x/a.py"))
        t.observe(tr(f"r{i}", error=False))
    t.observe(tu("Edit", "e0", file_path="/x/a.py"))
    t.observe(tr("e0", error=False))
    t.observe(tu("Read", "r9", file_path="/x/a.py"))
    assert t.observe(tr("r9", error=False)) is None


def test_error_sig_ignores_timestamps_and_addresses():
    a = ag.LoopTracker._error_sig("fail at 12:01:03 addr 0xdeadbeef took 1.23s")
    b = ag.LoopTracker._error_sig("fail at 23:59:59 addr 0xcafe0000 took 9.87s")
    assert a == b


def test_loop_paged_read_is_progress_not_loop():
    """guard.sh R3 pages large files at FOREMAN_READ_LINE_CAP; reading successive
    offsets of one file is progress. Only the SAME page repeated trips (2026-08-29,
    seen on wq#1095 a1: sentinel_probes.py 960 lines read in 3 pages -> KILLED)."""
    t = ag.LoopTracker(threshold=3)
    for i, off in enumerate((0, 300, 600, 900)):
        t.observe(tu("Read", f"p{i}", file_path="/x/big.py", offset=off, limit=300))
        assert t.observe(tr(f"p{i}", error=False, text="page")) is None
    trip = None
    for i in range(3):
        t.observe(tu("Read", f"s{i}", file_path="/x/big.py", offset=300, limit=300))
        trip = t.observe(tr(f"s{i}", error=False, text="page"))
    assert trip and "re-read big.py" in trip


# ---------------- wq#2869: tier ceiling on the enforce path -----------------

def _judge_says(tier, verdict, conditions=()):
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json, sys
        print(json.dumps({{"tier": {tier!r}, "verdict": {verdict!r},
                           "conditions": {list(conditions)!r}, "rationale": "fake",
                           "model": "fake-model", "latency_s": 0.01}}))
        sys.exit(0)
    """).replace("'None'", "None")


@pytest.mark.parametrize("tier,verdict,conds", [
    ("EXTREMELY HIGH", "allow", ()),
    ("EXTREMELY HIGH", "allow-with-conditions", ("snapshot before executing",)),
    ("High", "allow", ()),
    ("High", "allow-with-conditions", ("declared maintenance window", "Frank notified")),
    (None, "allow", ()),
    ("Catastrophic", "allow", ()),
])
def test_tier_ceiling_severe_or_unknown_tier_never_auto_allows(tmp_path, tier, verdict, conds):
    judge = _fake_judge(tmp_path, "fake_judge_tier.py", _judge_says(tier, verdict, conds))
    worker = "tier-ceiling"
    rc, err = run_hook(bash_payload("mkfs.ext4 /dev/sdz"), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        "FOREMAN_JUDGE_ENFORCE": "1", "FOREMAN_JUDGE_BIN": judge,
        "FOREMAN_WORKER": worker})
    assert rc == 2, f"{tier}/{verdict} must stay a hard RED block: {err}"
    assert "ACTION BLOCKED" in err
    kinds = [e["event"] for e in _events(tmp_path, worker)]
    assert kinds == ["gate_red_block", "judge_enforce", "gate_judge_deny"]


def test_tier_ceiling_medium_bare_allow_is_forced_to_snapshot(tmp_path):
    target = tmp_path / "secret.token"
    target.write_text("pre-image content\n")
    judge = _fake_judge(tmp_path, "fake_judge_medium.py", _judge_says("Medium", "allow"))
    worker = "tier-medium"
    rc, err = run_hook(bash_payload(f"shred -u {target}"), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        ag.SNAPDIR_ENV: str(tmp_path / "snaps"),
        "FOREMAN_JUDGE_ENFORCE": "1", "FOREMAN_JUDGE_BIN": judge,
        "FOREMAN_WORKER": worker})
    assert rc == 0, err
    events = _events(tmp_path, worker)
    assert [e["event"] for e in events] == ["gate_red_block", "judge_enforce",
                                            "gate_judge_allow_with_conditions"]
    staged = events[-1]["staged"]
    assert any("forced: Medium tier" in s["condition"] and s["artifacts"] for s in staged)


def test_tier_ceiling_low_allow_unchanged(tmp_path):
    judge = _fake_judge(tmp_path, "fake_judge_low.py", _judge_says("Low-Medium", "allow"))
    rc, err = run_hook(bash_payload("mkfs.ext4 /dev/sdz"), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        "FOREMAN_JUDGE_ENFORCE": "1", "FOREMAN_JUDGE_BIN": judge,
        "FOREMAN_WORKER": "tier-low"})
    assert rc == 0, err
