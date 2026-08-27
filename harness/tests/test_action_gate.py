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
    projects = tmp_path / "projects"
    _make_marker(str(projects))   # wq#991: research complete, to isolate the backup->AMBER path
    rc, _ = run_hook(bash_payload("fastboot flash boot boot.img"),
                     {ag.GATE_RULES_ENV: out, ag.SNAPDIR_ENV: str(snapdir),
                      "FOREMAN_PROJECTS_ROOT": str(projects),
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


# ---------------- wq#991: research-first phase gate ----------------

def _make_marker(projects_root, project="miko3", findings_bytes=600,
                 findings_name="PRIOR_ART.md", marker_project="miko3",
                 schema=ag.RESEARCH_SCHEMA, write_findings=True):
    rdir = os.path.join(projects_root, project, "research")
    os.makedirs(rdir, exist_ok=True)
    if write_findings:
        with open(os.path.join(rdir, findings_name), "w") as f:
            f.write("# PRIOR ART\n" + ("x" * findings_bytes))
    marker = {"schema": schema, "project": marker_project,
              "hardware_class": "unrecoverable", "completed_at": "2026-08-27T00:00:00Z",
              "engine": "fleet-search+searxng", "queries": ["miko 3 github"],
              "sources_ok": ["web"], "result_counts": {"web": 3}, "total_results": 3,
              "findings_file": findings_name, "findings_bytes": findings_bytes}
    with open(os.path.join(rdir, ag.RESEARCH_MARKER), "w") as f:
        json.dump(marker, f)
    return rdir


def test_research_gate_blocks_device_command_pre_research(tmp_path):
    """A fresh session on a hardware_class project cannot touch the device until
    research is done — even a read-only `adb shell getprop`."""
    out = compiled(tmp_path)
    projects = tmp_path / "projects"          # empty: no marker exists
    rc, err = run_hook(bash_payload("adb shell getprop ro.product.model"),
                       {ag.GATE_RULES_ENV: out, "FOREMAN_PROJECTS_ROOT": str(projects),
                        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 2, "device command allowed before research"
    assert "research phase required" in err
    assert "research_phase.py" in err


def test_research_gate_allows_after_valid_marker(tmp_path):
    out = compiled(tmp_path)
    projects = tmp_path / "projects"
    _make_marker(str(projects))
    rc, err = run_hook(bash_payload("adb shell getprop ro.product.model"),
                       {ag.GATE_RULES_ENV: out, "FOREMAN_PROJECTS_ROOT": str(projects),
                        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 0, f"device command still blocked after valid research marker :: {err}"


def test_research_marker_existence_alone_insufficient(tmp_path):
    """Marker present but findings file missing (a bare/forged marker) -> the
    validator rejects it and the device command stays blocked."""
    out = compiled(tmp_path)
    projects = tmp_path / "projects"
    _make_marker(str(projects), write_findings=False)   # marker only, no PRIOR_ART.md
    rc, err = run_hook(bash_payload("adb devices"),
                       {ag.GATE_RULES_ENV: out, "FOREMAN_PROJECTS_ROOT": str(projects),
                        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 2, "marker with no findings file should not satisfy the gate"


def test_research_marker_tiny_findings_insufficient(tmp_path):
    out = compiled(tmp_path)
    projects = tmp_path / "projects"
    _make_marker(str(projects), findings_bytes=10)      # < MIN_FINDINGS_BYTES
    rc, _ = run_hook(bash_payload("fastboot getvar all"),
                     {ag.GATE_RULES_ENV: out, "FOREMAN_PROJECTS_ROOT": str(projects),
                      ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 2, "trivially small findings should not satisfy the gate"


def test_research_marker_forge_via_bash_is_red(tmp_path):
    """The agent cannot self-clear the gate by writing the marker itself."""
    for cmd in (
        "touch /home/lumen/projects/miko3/research/.research-complete.json",
        "echo '{}' > /home/lumen/projects/miko3/research/.research-complete.json",
        "python3 -c \"open('/p/research/.research-complete.json','w').write('{}')\"",
    ):
        rc, err = run_hook(bash_payload(cmd),
                           {ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
        assert rc == 2, f"marker forge not blocked: {cmd}"
        assert "tamper" in err.lower() or "ACTION BLOCKED" in err


def test_research_marker_forge_via_write_is_red(tmp_path):
    rc, err = run_hook({"tool_name": "Write",
                        "tool_input": {"file_path": "/p/research/.research-complete.json",
                                       "content": "{}"}, "cwd": "/tmp"},
                       {ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 2
    assert "tamper" in err.lower()


def test_research_marker_read_is_allowed(tmp_path):
    """Reading the marker (cat/ls) is fine — only writes are tamper."""
    rc, _ = run_hook(bash_payload("cat /home/lumen/projects/miko3/research/.research-complete.json"),
                     {ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 0


def test_research_gate_non_device_command_allowed_pre_research(tmp_path):
    """During the research phase the agent may still read dumps, grep, plan,
    commit — only DEVICE commands are gated."""
    out = compiled(tmp_path)
    projects = tmp_path / "projects"          # no marker
    for cmd in ("cat /home/lumen/projects/miko3/gd32_dump.bin",
                "grep -rn miko /home/lumen/projects/miko3",
                "git commit -m 'chore: notes'",
                "python3 /home/lumen/foreman/harness/research_phase.py --project miko3"):
        rc, err = run_hook(bash_payload(cmd),
                           {ag.GATE_RULES_ENV: out, "FOREMAN_PROJECTS_ROOT": str(projects),
                            ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
        assert rc == 0, f"non-device command wrongly blocked pre-research: {cmd} :: {err}"


def test_research_gate_destructive_device_stays_red(tmp_path):
    """RED wins: a destructive device command is blocked-and-escalated even
    with no research, not softened into the self-service research block."""
    out = compiled(tmp_path)
    projects = tmp_path / "projects"          # no marker
    rc, err = run_hook(bash_payload("fastboot flash boot boot.img"),
                       {ag.GATE_RULES_ENV: out, "FOREMAN_PROJECTS_ROOT": str(projects),
                        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 2
    assert "hardware_class=unrecoverable" in err   # RED project-rule path, not research msg
    assert "research phase required" not in err


def test_research_gate_not_applied_without_optin(tmp_path):
    """A project with neither hardware_class nor a research phase_gate is NOT
    research-gated — no regression on ordinary (non-hardware) tickets."""
    out = compiled(tmp_path, "project: webapp\nrules:\n  - be tidy\n")
    projects = tmp_path / "projects"
    rc, _ = run_hook(bash_payload("adb shell getprop ro.build.version.sdk"),
                     {ag.GATE_RULES_ENV: out, "FOREMAN_PROJECTS_ROOT": str(projects),
                      ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 0, "non-hardware project should not be research-gated"


def test_research_gate_phasegate_alone_opts_in(tmp_path):
    """phase_gates:[research] with NO hardware_class still opts the project in."""
    out = compiled(tmp_path, "project: nest\nphase_gates: [research, implementation]\n")
    projects = tmp_path / "projects"
    rc, err = run_hook(bash_payload("adb shell dumpsys"),
                       {ag.GATE_RULES_ENV: out, "FOREMAN_PROJECTS_ROOT": str(projects),
                        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 2
    assert "research phase required" in err


def test_research_gate_no_rules_no_gate(tmp_path):
    """No compiled rules at all (e.g. a foreman ticket) -> device-ish command is
    not research-gated by this stage (guard.sh/builtins still apply)."""
    rc, _ = run_hook(bash_payload("adb devices"),
                     {ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 0


# ---------------- wq#991: is_device_command unit ----------------

@pytest.mark.parametrize("cmd", [
    "adb shell getprop", "fastboot getvar all", "heimdall detect",
    "flashrom -p linux_spi -r dump.bin", "rkdeveloptool rfi",
    "edl printgpt", "esptool.py flash_id", "openocd -f board.cfg",
    "screen /dev/ttyUSB0 115200", "picocom -b 115200 /dev/ttyUSB1",
])
def test_is_device_command_true(cmd):
    assert ag.is_device_command(cmd, {}) is True, cmd


@pytest.mark.parametrize("cmd", [
    "lsusb", "dmesg | tail", "ls /dev", "cat dump.bin", "grep foo bar.txt",
    "python3 tool.py", "git status", "fleet-search web 'miko 3'",
])
def test_is_device_command_false(cmd):
    assert ag.is_device_command(cmd, {}) is False, cmd


def test_device_patterns_additive(tmp_path):
    """Per-project device_patterns extend the builtin list."""
    yaml_text = ("project: screenpad\nhardware_class: risky\n"
                 "device_patterns:\n  - pattern: '\\brtd-isp\\b'\n    reason: RTD ISP flasher\n")
    out = compiled(tmp_path, yaml_text)
    projects = tmp_path / "projects"
    rc, err = run_hook(bash_payload("rtd-isp --write fw.bin"),
                       {ag.GATE_RULES_ENV: out, "FOREMAN_PROJECTS_ROOT": str(projects),
                        ag.HANDOFF_ENV: str(tmp_path), "FOREMAN_EVENTS_DIR": str(tmp_path)})
    assert rc == 2
    assert "research phase required" in err
