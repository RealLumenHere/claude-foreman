"""Tests for action_gate.py — TIERED_INTELLIGENCE_DESIGN.md Layers 1-3 (wq#985).
Test-to-failure: prove RED blocks, prove AMBER snapshots (and BLOCKS on snapshot
failure), prove the loop breaker trips (and does NOT trip on a healthy TDD loop),
prove project rules are additive, prove the hook fails closed."""
import hashlib
import json
import os
import re
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import action_gate as ag
import energize_confirm as ec
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


def mcp_payload(tool_name, tool_input=None, cwd="/tmp"):
    return {"tool_name": tool_name, "tool_input": tool_input or {}, "cwd": cwd}


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
    # wq#4746 (wq#4744 T1 gap-analysis §2(a)): /dev/mtd*, /dev/mem raw targets
    "cat img.bin > /dev/mtd0",
    "cat payload.bin > /dev/mem",
    # eFuse burn is unconditional RED (irreversible, no backup helps)
    "espefuse.py burn_efuse FLASH_CRYPT_CNT 1",
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

# wq#4748 T4: proves the hardware floor denies BEFORE the judge is consulted --
# this script would both allow (Low tier) AND leave a trace it ran, so a test
# asserting the touchfile is absent proves the call never happened at all.
FAKE_JUDGE_ALLOW_TOUCH = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    open(os.environ["FAKE_JUDGE_TOUCHFILE"], "w").close()
    print(json.dumps({"tier": "Low", "verdict": "allow", "conditions": [],
                       "rationale": "fake allow", "model": "fake-model", "latency_s": 0.01}))
    sys.exit(0)
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


# ---------------- wq#4748 T4: hardware floor overrides judge enforce --------
# A RED from BUILTIN_FLASH, the research gate, or a hardware_class project
# rule must deny even when FOREMAN_JUDGE_ENFORCE=1 and the judge would allow
# -- and the judge binary must never even be called (proven via the touchfile
# a real call would leave behind), matching every other judge-enforce test's
# "fails closed, no exceptions" posture.

def test_judge_enforce_hardware_floor_denies_builtin_flash(tmp_path):
    """No backup declared at all (and no hardware_class/research gate in play)
    -- isolates the BUILTIN_FLASH branch itself from the research-gate branch,
    which runs first in classify_bash when both would otherwise fire."""
    touchfile = tmp_path / "touched"
    judge = _fake_judge(tmp_path, "fake_judge_allow_touch.py", FAKE_JUDGE_ALLOW_TOUCH)
    rc, err = run_hook(bash_payload("fastboot flash boot boot.img"), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        "FOREMAN_JUDGE_ENFORCE": "1", "FOREMAN_JUDGE_BIN": judge,
        "FAKE_JUDGE_TOUCHFILE": str(touchfile), "FOREMAN_WORKER": "hw-floor-flash"})
    assert rc == 2, f"BUILTIN_FLASH RED must deny under the hardware floor: {err}"
    assert "ACTION BLOCKED" in err
    assert not touchfile.exists(), "judge binary was called for a hardware-floor RED"
    kinds = [e["event"] for e in _events(tmp_path, "hw-floor-flash")]
    assert kinds == ["gate_red_block", "gate_hardware_floor_deny"]


def test_judge_enforce_hardware_floor_denies_research_gate(tmp_path):
    touchfile = tmp_path / "touched"
    judge = _fake_judge(tmp_path, "fake_judge_allow_touch2.py", FAKE_JUDGE_ALLOW_TOUCH)
    out = compiled(tmp_path, GATEPROJ_RULES)
    rc, err = run_hook(bash_payload("adb shell getprop ro.build.fingerprint"), {
        ag.GATE_RULES_ENV: out, ag.HANDOFF_ENV: str(tmp_path),
        "FOREMAN_EVENTS_DIR": str(tmp_path), "FOREMAN_JUDGE_ENFORCE": "1",
        "FOREMAN_JUDGE_BIN": judge, "FAKE_JUDGE_TOUCHFILE": str(touchfile),
        "FOREMAN_WORKER": "hw-floor-research"})
    assert rc == 2, f"research gate RED must deny under the hardware floor: {err}"
    assert "ACTION BLOCKED" in err
    assert not touchfile.exists(), "judge binary was called for a hardware-floor RED"
    kinds = [e["event"] for e in _events(tmp_path, "hw-floor-research")]
    assert kinds == ["gate_red_block", "gate_hardware_floor_deny"]


def test_judge_enforce_hardware_floor_denies_project_rule(tmp_path):
    """A hardware_class project red_pattern (miko-tool --reset) is also floored."""
    touchfile = tmp_path / "touched"
    judge = _fake_judge(tmp_path, "fake_judge_allow_touch3.py", FAKE_JUDGE_ALLOW_TOUCH)
    out = compiled(tmp_path)  # MIKO_RULES: hardware_class: unrecoverable
    rc, err = run_hook(bash_payload("miko-tool --reset --confirm"), {
        ag.GATE_RULES_ENV: out, ag.HANDOFF_ENV: str(tmp_path),
        "FOREMAN_EVENTS_DIR": str(tmp_path), "FOREMAN_JUDGE_ENFORCE": "1",
        "FOREMAN_JUDGE_BIN": judge, "FAKE_JUDGE_TOUCHFILE": str(touchfile),
        "FOREMAN_WORKER": "hw-floor-project-rule"})
    assert rc == 2, f"hardware_class project rule RED must deny under the floor: {err}"
    assert "ACTION BLOCKED" in err
    assert not touchfile.exists(), "judge binary was called for a hardware-floor RED"
    kinds = [e["event"] for e in _events(tmp_path, "hw-floor-project-rule")]
    assert kinds == ["gate_red_block", "gate_hardware_floor_deny"]


# wq#4756 (wq#4754 verify-pass on wq#4748): BUILTIN_RED had no per-entry floor
# tag, so wq#4746's eFuse-burn row -- put there precisely because
# BUILTIN_FLASH's verified-backup escape hatch is unsound for a fuse burn --
# was judge-eligible. The floored patterns now live in BUILTIN_HARDWARE_RED.

# One representative command per BUILTIN_HARDWARE_RED pattern. The invariant
# test below fails if a pattern is added without one, so a future
# one-time-irreversible row cannot land untested (wq#2630).
HARDWARE_RED_CASES = [
    "espefuse.py burn_efuse FLASH_CRYPT_CNT 1",
]


def test_builtin_hardware_red_every_pattern_has_a_case():
    for pat, _reason in ag.BUILTIN_HARDWARE_RED:
        assert any(re.search(pat, c, re.IGNORECASE) for c in HARDWARE_RED_CASES), (
            f"BUILTIN_HARDWARE_RED pattern {pat!r} has no HARDWARE_RED_CASES "
            f"representative -- add one so the judge-enforce floor is proven")


@pytest.mark.parametrize("cmd", HARDWARE_RED_CASES)
def test_judge_enforce_hardware_floor_denies_builtin_hardware_red(tmp_path, cmd):
    """No hardware_class project, no flash/research gate in play -- isolates the
    BUILTIN_HARDWARE_RED table itself as the source of the floor."""
    touchfile = tmp_path / "touched"
    judge = _fake_judge(tmp_path, "fake_judge_allow_touch4.py", FAKE_JUDGE_ALLOW_TOUCH)
    rc, err = run_hook(bash_payload(cmd), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        "FOREMAN_JUDGE_ENFORCE": "1", "FOREMAN_JUDGE_BIN": judge,
        "FAKE_JUDGE_TOUCHFILE": str(touchfile), "FOREMAN_WORKER": "hw-floor-hwred"})
    assert rc == 2, f"BUILTIN_HARDWARE_RED must deny under the hardware floor: {err}"
    assert "ACTION BLOCKED" in err
    assert not touchfile.exists(), "judge binary was called for a hardware-floor RED"
    kinds = [e["event"] for e in _events(tmp_path, "hw-floor-hwred")]
    assert kinds == ["gate_red_block", "gate_hardware_floor_deny"]


@pytest.mark.parametrize("cmd,expect_floor", [
    # inside the fix's table
    ("espefuse.py burn_efuse FLASH_CRYPT_CNT 1", True),
    # outside it: wq#4746's sibling verb, which landed in BUILTIN_FLASH
    ("esptool.py erase_flash", True),
    # outside it: plain destructive BUILTIN_RED stays judge-eligible
    ("mkfs.ext4 /dev/sdz", False),
    ("dd if=/dev/zero of=/dev/sda bs=4M", False),
])
def test_classify_bash_hw_floor_by_table(cmd, expect_floor):
    verdict, _reason, _snap, hw_floor = ag.classify_bash(cmd, {})
    assert verdict == "RED", cmd
    assert hw_floor is expect_floor, cmd


def test_judge_enforce_non_hardware_red_still_reaches_judge(tmp_path):
    """Control: a plain BUILTIN_RED (no hardware project, no flash/research gate)
    must still be judge-eligible -- the floor must not swallow every RED."""
    judge = _fake_judge(tmp_path, "fake_judge_allow.py", FAKE_JUDGE_ALLOW)
    rc, err = run_hook(bash_payload("mkfs.ext4 /dev/sdz"), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        "FOREMAN_JUDGE_ENFORCE": "1", "FOREMAN_JUDGE_BIN": judge,
        "FOREMAN_WORKER": "non-hw-floor"})
    assert rc == 0, f"a non-hardware RED must still be judge-eligible: {err}"
    kinds = [e["event"] for e in _events(tmp_path, "non-hw-floor")]
    assert kinds == ["gate_red_block", "judge_enforce", "gate_judge_allow"]


# ---------------- Layer 1: GREEN passes ----------------

GREEN_CASES = [
    "ls -la /home/lumen",
    "git commit -m 'feat: thing'",
    "git push origin feat/x",
    "grep -rn pattern src/",
    "python3 -m pytest tests/ -x",
    "rm -rf /tmp/scratch/build",
    "fdisk -l",
    # wq#4746: read-side verbs for the new flasher classes must stay GREEN
    "esptool.py read_flash 0x0 0x1000 dump.bin",
    "espefuse.py summary",
    "st-flash read dump.bin 0x8000000 0x10000",
    "stm32flash -r dump.bin /dev/ttyUSB0",
    "avrdude -c usbasp -p m328p -U flash:r:dump.hex:i",
    "dfu-util -U dump.bin",
    "rkdeveloptool rl 0x0 0x1000 dump.img",
    "xfel spinand read 0x0 0x1000 dump.bin",
    "mtk.py r boot_a dump.img",
    "flashrom -r dump.bin",
    "nanddump -f dump.bin /dev/mtd0",
    "mtd_debug read /dev/mtd0 0 1024 dump.bin",
]


@pytest.mark.parametrize("cmd", GREEN_CASES)
def test_green_passes(cmd):
    rc, err = run_hook(bash_payload(cmd))
    assert rc == 0, f"GREEN blocked: {cmd} :: {err}"


# ---------------- mcp__ hardware MCP tools (wq#4747) ------------------------
# docs/hardware-re-safety/gap-analysis.md (a'): no hook matcher covered mcp__*
# calls at all. These prove the PreToolUse dispatch for mcp__ tools, and that
# an energizing capability is RED without a valid operator-issued confirm
# token (hal-spec.md §9.2a) -- a model-supplied bool is not accepted.

MCP_ENERGIZE_CASES = [
    ("mcp__openocd__write_target_memory", "openocd.write_target_memory", {"address": 0, "data": [1]}),
    ("mcp__openocd__flash_firmware", "openocd.flash_firmware", {"image_path": "/tmp/x.bin", "address": 0}),
    ("mcp__limpet-flashrom__write_flash", "flashrom.write_flash",
     {"programmer": "ch341a_spi", "input_path": "/tmp/x.bin"}),
    ("mcp__limpet-flashrom__erase_chip", "flashrom.erase_chip", {"programmer": "ch341a_spi"}),
    ("mcp__serial-maksoor__write", "serial.write", {"port": "/dev/ttyUSB0", "data": "AT\r\n"}),
]


@pytest.mark.parametrize("tool_name,capability,extra_input", MCP_ENERGIZE_CASES)
def test_mcp_energize_blocked_without_token(tmp_path, tool_name, capability, extra_input):
    rc, err = run_hook(mcp_payload(tool_name, extra_input), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 2
    assert "confirm_token" in err
    assert capability in err


@pytest.mark.parametrize("tool_name,capability,extra_input", MCP_ENERGIZE_CASES)
def test_mcp_energize_allowed_with_valid_token(tmp_path, monkeypatch, tool_name, capability, extra_input):
    token_dir = str(tmp_path / ".energize-confirm")
    monkeypatch.setattr(ec, "TOKEN_DIR", token_dir)
    token = ec.issue_token(capability, _isatty=True)
    payload_input = dict(extra_input)
    payload_input["confirm_token"] = token
    rc, err = run_hook(mcp_payload(tool_name, payload_input), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        "ENERGIZE_CONFIRM_DIR": token_dir,
    })
    assert rc == 0, err


def test_mcp_energize_token_is_single_use_at_the_hook(tmp_path, monkeypatch):
    token_dir = str(tmp_path / ".energize-confirm")
    monkeypatch.setattr(ec, "TOKEN_DIR", token_dir)
    token = ec.issue_token("flashrom.erase_chip", _isatty=True)
    env = {ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
           "ENERGIZE_CONFIRM_DIR": token_dir}
    payload = mcp_payload("mcp__limpet-flashrom__erase_chip",
                           {"programmer": "ch341a_spi", "confirm_token": token})
    rc1, err1 = run_hook(payload, env)
    rc2, err2 = run_hook(payload, env)  # replay the same token
    assert rc1 == 0, err1
    assert rc2 == 2
    assert "confirm_token" in err2


def test_mcp_non_hardware_tool_is_green(tmp_path):
    rc, err = run_hook(mcp_payload("mcp__keenable__search_web_pages", {"query": "x"}), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 0, err


def test_mcp_hardware_read_only_tool_is_green(tmp_path):
    rc, err = run_hook(mcp_payload("mcp__openocd__read_target_memory", {"address": 0}), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 0, err


def test_mcp_serial_list_ports_is_green(tmp_path):
    rc, err = run_hook(mcp_payload("mcp__serial-maksoor__list_ports", {}), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 0, err


def test_mcp_serial_unclassified_tool_defaults_to_red(tmp_path):
    # hal-spec.md §9.2a: an unconfirmed/unclassified capability defaults to
    # energizing. serial-mcp is a pinned third-party binary with no source in
    # this repo, so a tool name this gate has never seen (not "write", not
    # anything on SERIAL_MCP_READ_ONLY_TOOLS) must fail closed, not open.
    rc, err = run_hook(mcp_payload("mcp__serial-maksoor__some_future_tool", {"port": "/dev/ttyUSB0"}), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 2
    assert "confirm_token" in err


def test_mcp_energize_red_is_never_judge_overridden(tmp_path):
    # hal-spec.md §9.1: this policy "cannot be overridden ... by prompt
    # engineering" -- the mcp__ RED path returns before enforce_judge_verdict,
    # so FOREMAN_JUDGE_ENFORCE=1 changes nothing even with a judge that would
    # rubber-stamp anything.
    rc, err = run_hook(mcp_payload("mcp__openocd__write_target_memory", {"address": 0, "data": [1]}), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path),
        "FOREMAN_JUDGE_ENFORCE": "1", "FOREMAN_JUDGE_BIN": "/bin/true",
    })
    assert rc == 2
    assert "confirm_token" in err


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


# ---------------- wq#4654: vault retention for non-credential pre-images -----

def _age(path, days):
    t = os.path.getmtime(path) - days * 86400
    os.utime(path, (t, t))


def test_prune_vault_deletes_old_non_credential_preimage(tmp_path):
    """Basename-only diverts (e.g. `prod.env` with no token inside) still land in the
    vault, but secret-backup-sweep's value-level dead-check never claims them (verdict
    NO_SECRETS, excluded from cmd_apply's todo list). prune_vault is the age-based
    backstop for exactly that file."""
    vault = tmp_path / "vault"
    vault.mkdir()
    f = vault / "worker-20260101T000000Z-prod.env"
    f.write_text("FEATURE_FLAG=1\n")
    _age(f, 31)
    removed = ag.prune_vault(max_age_days=30, vault=str(vault))
    assert removed == [str(f)]
    assert not f.exists()


def test_prune_vault_keeps_recent_non_credential_preimage(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    f = vault / "worker-20260101T000000Z-prod.env"
    f.write_text("FEATURE_FLAG=1\n")
    _age(f, 2)
    removed = ag.prune_vault(max_age_days=30, vault=str(vault))
    assert removed == []
    assert f.exists()


def test_prune_vault_keeps_old_credential_bearing_preimage(tmp_path):
    """A pre-image that still carries a trackable secret value is secret-backup-sweep's
    to retire (value-level dead-check, wq#4599's 7d TTL) — prune_vault must not race it."""
    vault = tmp_path / "vault"
    vault.mkdir()
    f = vault / "worker-20260101T000000Z-agent.env"
    f.write_text(f"GITHUB_TOKEN={FAKE_PAT}\n")
    _age(f, 31)
    removed = ag.prune_vault(max_age_days=30, vault=str(vault))
    assert removed == []
    assert f.exists()


def test_prune_vault_missing_dir_is_a_noop(tmp_path):
    assert ag.prune_vault(max_age_days=30, vault=str(tmp_path / "no-such-vault")) == []


# ---------------- wq#4706: dry-run, so the scheduler has a read-only probe -----

def test_prune_vault_dry_run_reports_without_deleting(tmp_path):
    """The wq#4706 ops bundle's --verify and install-time pre-check have to ask the REAL
    vault what this pass would do; an installer that must delete to prove itself is no
    pre-flight check. Same selection, no unlink."""
    vault = tmp_path / "vault"
    vault.mkdir()
    doomed = vault / "worker-20260101T000000Z-prod.env"
    doomed.write_text("FEATURE_FLAG=1\n")
    _age(doomed, 31)
    kept = vault / "worker-20260101T000000Z-agent.env"
    kept.write_text(f"GITHUB_TOKEN={FAKE_PAT}\n")
    _age(kept, 31)
    reported = ag.prune_vault(max_age_days=30, vault=str(vault), dry_run=True)
    assert reported == [str(doomed)]
    assert doomed.exists() and kept.exists()
    # and the wet pass agrees with what the dry one promised
    assert ag.prune_vault(max_age_days=30, vault=str(vault)) == [str(doomed)]
    assert not doomed.exists() and kept.exists()


def test_prune_vault_cli_dry_run_leaves_the_vault_alone(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    f = vault / "worker-20260101T000000Z-prod.env"
    f.write_text("FEATURE_FLAG=1\n")
    _age(f, 31)
    env = dict(os.environ, FOREMAN_GATE_SNAP_VAULT=str(vault))
    r = subprocess.run([sys.executable, ag.__file__, "prune-vault", "--dry-run"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "would prune 1 file(s)" in r.stdout
    assert f.exists()


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


def _miko_target(tmp_path, sha=None, extra=""):
    """A complete wq#4751 target manifest whose recovery image verifies."""
    img = tmp_path / "full_flash.bin"
    img.write_bytes(b"\x00" * 1024)
    layout = tmp_path / "layout.txt"
    layout.write_text("0x0 bootloader\n0x10000 app\n")
    sha = sha or hashlib.sha256(img.read_bytes()).hexdigest()
    return textwrap.dedent(f"""\
        targets:
          - id: miko3-board
            datasheet: https://example.invalid/miko3-soc.pdf
            flash_layout: {layout}
            recovery_image: {img}
            recovery_sha256: {sha}
        """) + extra


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
    out = compiled(tmp_path, MIKO_RULES + _miko_target(tmp_path))
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


# ---------------- wq#4746: MCU/SoC flasher write/erase class ----------------
# gap-analysis.md §2(a): BUILTIN_FLASH lacked these tools' write/erase verbs.
# Backup-conditional, same as fastboot flash above — RED with no declared
# backup (default env, no rules file), AMBER once a verified local backup
# exists (see test_flash_class_ambers_with_local_backup below).

FLASH_CLASS_RED_CASES = [
    "flashrom -p internal -E",
    "esptool.py write_flash 0x0 firmware.bin",
    "esptool.py erase_flash",
    "openocd -f board.cfg -c 'program firmware.elf verify reset exit'",
    "openocd -f board.cfg -c 'flash erase_sector 0 0 last'",
    "st-flash write firmware.bin 0x8000000",
    "st-flash erase",
    "stm32flash -w firmware.bin /dev/ttyUSB0",
    "stm32flash -e 4 /dev/ttyUSB0",
    "avrdude -c usbasp -p m328p -U flash:w:firmware.hex:i",
    "avrdude -c usbasp -p m328p -e",
    "dfu-util -D firmware.bin -a 0",
    "rkdeveloptool wl 0x0 firmware.img",
    "rkdeveloptool ef",
    "upgrade_tool ef",
    "sunxi-fel spiflash-write 0x0 firmware.bin",
    "xfel spinand write 0x0 firmware.bin",
    "mtk.py w boot_a boot_a.img",
    "mtk.py e boot_a",
    "flashcp firmware.bin /dev/mtd0",
    "nandwrite -p /dev/mtd0 firmware.bin",
    "flash_erase /dev/mtd0 0 0",
    "mtd_debug write /dev/mtd0 0 1024 firmware.bin",
]


@pytest.mark.parametrize("cmd", FLASH_CLASS_RED_CASES)
def test_flash_class_red_without_backup(tmp_path, cmd):
    rc, err = run_hook(bash_payload(cmd), {
        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 2, f"flash-class write/erase not blocked without a backup: {cmd}"
    assert "ACTION BLOCKED" in err


def test_flash_class_ambers_with_local_backup(tmp_path):
    out = compiled(tmp_path, MIKO_RULES + _miko_target(tmp_path))
    research = tmp_path / "proj" / "research"
    research.mkdir(parents=True, exist_ok=True)
    (research / "PRIOR_ART.md").write_text("## finding\nprior art on file\n")
    snapdir = tmp_path / "snaps"
    rc, _ = run_hook(bash_payload("esptool.py write_flash 0x0 firmware.bin"),
                     {ag.GATE_RULES_ENV: out, ag.SNAPDIR_ENV: str(snapdir),
                      "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 0, "verified local backup should downgrade flasher write to AMBER"


# ---------------- wq#4751: per-target hardware evidence manifest ----------------
# gap-analysis.md §2(b): on a hardware_class project a write-class action needs a
# declared target whose recovery image hashes to its declared sha256 at hook time;
# a bare non-empty backup file no longer opens the gate there.

def _hw_env(tmp_path, out):
    research = tmp_path / "proj" / "research"
    research.mkdir(parents=True, exist_ok=True)
    (research / "PRIOR_ART.md").write_text("## finding\nprior art on file\n")
    return {ag.GATE_RULES_ENV: out, ag.SNAPDIR_ENV: str(tmp_path / "snaps"),
            ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)}


FLASH_CMD = "esptool.py write_flash 0x0 firmware.bin"


def test_hw_bare_backup_no_longer_opens_write(tmp_path):
    dump = tmp_path / "full_flash.bin"
    dump.write_bytes(b"\x00" * 1024)
    out = compiled(tmp_path, MIKO_RULES + f"backups:\n  - {dump}\n")
    rc, err = run_hook(bash_payload(FLASH_CMD), _hw_env(tmp_path, out))
    assert rc == 2
    assert "no target manifest is declared" in err


def test_hw_recovery_sha_mismatch_blocks(tmp_path):
    out = compiled(tmp_path, MIKO_RULES + _miko_target(tmp_path, sha="ab" * 32))
    rc, err = run_hook(bash_payload(FLASH_CMD), _hw_env(tmp_path, out))
    assert rc == 2
    assert "does not match declared" in err


def test_hw_recovery_image_changed_after_declaration_blocks(tmp_path):
    out = compiled(tmp_path, MIKO_RULES + _miko_target(tmp_path))
    (tmp_path / "full_flash.bin").write_bytes(b"\xff" * 1024)   # swapped/corrupted
    rc, err = run_hook(bash_payload(FLASH_CMD), _hw_env(tmp_path, out))
    assert rc == 2
    assert "does not match declared" in err


def test_hw_recovery_image_missing_blocks(tmp_path):
    out = compiled(tmp_path, MIKO_RULES + _miko_target(tmp_path))
    (tmp_path / "full_flash.bin").unlink()
    rc, err = run_hook(bash_payload(FLASH_CMD), _hw_env(tmp_path, out))
    assert rc == 2
    assert "not a file on local disk" in err


def test_hw_incomplete_manifest_blocks(tmp_path):
    text = _miko_target(tmp_path).replace(
        "    datasheet: https://example.invalid/miko3-soc.pdf\n", "")
    out = compiled(tmp_path, MIKO_RULES + text)
    rc, err = run_hook(bash_payload(FLASH_CMD), _hw_env(tmp_path, out))
    assert rc == 2
    assert "missing datasheet" in err


def test_hw_flash_layout_path_must_exist(tmp_path):
    out = compiled(tmp_path, MIKO_RULES + _miko_target(tmp_path))
    (tmp_path / "layout.txt").unlink()
    rc, err = run_hook(bash_payload(FLASH_CMD), _hw_env(tmp_path, out))
    assert rc == 2
    assert "flash_layout" in err


def test_hw_backup_required_token_needs_manifest(tmp_path):
    out = compiled(tmp_path)
    rc, err = run_hook(bash_payload("adb push /system/framework.jar /system/"),
                       _hw_env(tmp_path, out))
    assert rc == 2
    assert "target evidence" in err


def test_hw_multi_target_match_selects(tmp_path):
    other = tmp_path / "esp.bin"
    other.write_bytes(b"esp")
    extra = ("  - id: esp32-devkit\n"
             "    match: ['--port /dev/ttyUSB1']\n"
             "    datasheet: https://example.invalid/esp32.pdf\n"
             "    flash_layout: https://example.invalid/partitions.csv\n"
             f"    recovery_image: {other}\n"
             f"    recovery_sha256: {hashlib.sha256(b'esp').hexdigest()}\n")
    text = _miko_target(tmp_path, extra=extra).replace(
        "  - id: miko3-board\n", "  - id: miko3-board\n    match: ['--port /dev/ttyUSB0']\n")
    out = compiled(tmp_path, MIKO_RULES + text)
    env = _hw_env(tmp_path, out)
    rc, _ = run_hook(bash_payload("esptool.py --port /dev/ttyUSB1 write_flash 0x0 a.bin"), env)
    assert rc == 0
    rc, err = run_hook(bash_payload("esptool.py --port /dev/ttyUSB7 write_flash 0x0 a.bin"), env)
    assert rc == 2
    assert "names none of the declared targets" in err


def test_non_hw_backup_sha256_entry_verified(tmp_path):
    dump = tmp_path / "dump.bin"
    dump.write_bytes(b"dump")
    good = hashlib.sha256(b"dump").hexdigest()
    env = {ag.SNAPDIR_ENV: str(tmp_path / "snaps"), ag.HANDOFF_ENV: str(tmp_path),
           "FOREMAN_EVENTS_DIR": str(tmp_path)}
    out = compiled(tmp_path, f"project: plain\nbackups:\n  - path: {dump}\n    sha256: {good}\n")
    rc, _ = run_hook(bash_payload(FLASH_CMD), dict(env, **{ag.GATE_RULES_ENV: out}))
    assert rc == 0
    out = compiled(tmp_path, f"project: plain\nbackups:\n  - path: {dump}\n    sha256: {'0' * 64}\n")
    rc, _ = run_hook(bash_payload(FLASH_CMD), dict(env, **{ag.GATE_RULES_ENV: out}))
    assert rc == 2


PROTECTED_RED = [
    "rm full_flash.bin",
    "rm -f {d}/full_flash.bin",
    "mv full_flash.bin old.bin",
    "cp new.bin full_flash.bin",
    "echo x > full_flash.bin",
    "truncate -s 0 {d}/full_flash.bin",
    "sed -i s/a/b/ full_flash.bin",
    "esptool.py read_flash 0 0x400000 full_flash.bin",
    "flashrom -p ch341a_spi -r {d}/full_flash.bin",
    "avrdude -c usbasp -p m328p -U flash:r:full_flash.bin:r",
    "rm -rf {d}/dumps",
    "cp new.bin {d}/dumps/known.bin",
]

PROTECTED_GREEN = [
    "sha256sum full_flash.bin",
    "xxd full_flash.bin | head",
    "cp full_flash.bin /tmp/copy.bin",
    "esptool.py read_flash 0 0x400000 fresh-read.bin",
]


def _protected_rules(tmp_path):
    (tmp_path / "dumps").mkdir(exist_ok=True)
    return compiled(tmp_path, MIKO_RULES + _miko_target(
        tmp_path, extra=f"    known_good_dumps: [{tmp_path / 'dumps'}]\n"))


@pytest.mark.parametrize("cmd", PROTECTED_RED)
def test_known_good_dump_write_protected(tmp_path, cmd):
    out = _protected_rules(tmp_path)
    rc, err = run_hook(bash_payload(cmd.format(d=tmp_path), cwd=str(tmp_path)),
                       _hw_env(tmp_path, out))
    assert rc == 2, cmd
    assert "write-protected" in err


@pytest.mark.parametrize("cmd", PROTECTED_GREEN)
def test_known_good_dump_reads_pass(tmp_path, cmd):
    out = _protected_rules(tmp_path)
    rules = json.load(open(out))
    assert ag._protected_mutation(cmd, rules, str(tmp_path)) is None, cmd


def test_flash_from_recovery_image_allowed(tmp_path):
    out = _protected_rules(tmp_path)
    rc, err = run_hook(bash_payload("esptool.py write_flash 0x0 full_flash.bin",
                                    cwd=str(tmp_path)), _hw_env(tmp_path, out))
    assert rc == 0, err


def test_known_good_dump_file_tool_protected(tmp_path):
    out = _protected_rules(tmp_path)
    env = _hw_env(tmp_path, out)
    for path in (tmp_path / "full_flash.bin", tmp_path / "dumps" / "x.bin"):
        rc, err = run_hook({"tool_name": "Write", "tool_input": {"file_path": str(path)}}, env)
        assert rc == 2 and "write-protected" in err
    rc, _ = run_hook({"tool_name": "Write",
                      "tool_input": {"file_path": str(tmp_path / "notes.md")}}, env)
    assert rc == 0


def test_hw_registry_applies_by_repo_root(tmp_path, monkeypatch):
    reg = tmp_path / "hw_rules"
    reg.mkdir()
    repo = tmp_path / "re"
    (repo / "esp32").mkdir(parents=True)
    (reg / "re.yaml").write_text(f"repos: [{repo}]\nproject: re\nhardware_class: x\n"
                                 "rules:\n  - phase_gates: [research]\n")
    monkeypatch.setattr(ag, "HW_RULES_DIR", str(reg))
    out = ag.compile_rules([str(repo / "esp32")], str(tmp_path / "o.json"))
    data = json.load(open(out))
    assert data["hardware_class"] == "x"
    assert str(repo / "esp32" / "research" / "PRIOR_ART.md") in data["prior_art_paths"]
    other = tmp_path / "rest"
    other.mkdir()
    assert ag.compile_rules([str(other)], str(tmp_path / "o2.json")) is None


@pytest.mark.parametrize("repo,project", [
    ("/home/lumen/projects/limpet", "limpet"),
    ("/home/lumen/re/esp32-sandbox", "re"),
    ("/home/lumen/re", "re"),
])
def test_shipped_hw_rules_cover_re_repos(tmp_path, repo, project):
    """The committed harness/hw_rules files arm the research gate and the
    manifest requirement for the Limpet/Telpo/ESP32 repos, fail-closed."""
    data = json.load(open(ag.compile_rules([repo], str(tmp_path / "o.json"))))
    assert data["project"] == project and data["hardware_class"]
    assert ag._research_gate_required(data)
    assert ag._hw_write_evidence(FLASH_CMD, data)[0] is None


@pytest.mark.parametrize("repo", ["/home/lumen/foreman", "/home/lumen/projects/limpetx",
                                  "/home/lumen/reverse"])
def test_shipped_hw_rules_leave_other_repos_alone(tmp_path, repo):
    assert ag._hw_registry_sources([repo]) == []


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
