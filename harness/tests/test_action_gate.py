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

GATE = os.path.join(os.path.dirname(__file__), "..", "action_gate.py")


def run_hook(payload, env_extra=None):
    env = dict(os.environ)
    env.pop(ag.GATE_RULES_ENV, None)
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
