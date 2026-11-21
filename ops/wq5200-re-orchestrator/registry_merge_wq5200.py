#!/usr/bin/env python3
"""wq#5200: merge Limpet capability-router (re-orchestrate) config into the wq#5187 registry.

MERGE, never rewrite (registry _maintainers rule):
  * adds top-level  "orchestration"  (policy, defaults, stage order, category->class map)
  * adds per-tool   tools[k]["orchestration"]  (stage, action_class, runner, invocation, limits)
  * every other key is left as loaded; tools absent from the registry are skipped
  * idempotent: re-running yields the same file; --check exits 1 if a merge would change anything

Backs up the registry to /data/lumen/backups/wq5200-registry-<ts>.json before writing.

Usage: registry_merge_wq5200.py [--check] [--registry PATH]
"""
import argparse, json, shutil, sys, time
from pathlib import Path

REG = Path("/home/lumen/re/toolkit-expansion/capability-registry.json")
BACKUP_DIR = Path("/data/lumen/backups")
GB = 1024

ORCH = {
    "version": "1.0",
    "source_ticket": "wq#5200",
    "runner": {"host": "maksoor", "ssh": "maksoor", "work_root": "/data/re-work",
               "runner_path": "~/.local/lib/re-orchestrate/re-exec",
               "note": "router runs on Lumen; every tool runs on the runner host under re-exec (systemd scope + bwrap)"},
    "defaults": {"limits": {"timeout_s": 300, "memory_mb": 4 * GB, "disk_mb": 10 * GB, "tasks_max": 2048}},
    "stage_order": ["extract", "runtime_recovery", "static", "capability", "forensics",
                    "firmware_analysis", "dynamic", "symbolic", "fuzzing", "hardware"],
    "stages": {
        "extract": {"mode": "first_success", "auto": True, "purpose": "unpack containers / filesystems / APK resources"},
        "runtime_recovery": {"mode": "all", "auto": True, "purpose": "runtime-specific recovery (Go, Flutter, Hermes, IL2CPP, Godot, Python)"},
        "static": {"mode": "first_success", "auto": True, "purpose": "disassembly / decompilation / symbols / strings"},
        "capability": {"mode": "all", "auto": True, "purpose": "behaviour + capability detection"},
        "forensics": {"mode": "first_success", "auto": True, "purpose": "memory image forensics"},
        "firmware_analysis": {"mode": "first_success", "auto": False, "purpose": "whole-firmware security analysis (EMBA)"},
        "dynamic": {"mode": "first_success", "auto": False, "purpose": "emulation / record-replay / instrumentation (executes target code)"},
        "symbolic": {"mode": "first_success", "auto": False, "purpose": "symbolic execution / taint (on demand, --deep)"},
        "fuzzing": {"mode": "first_success", "auto": False, "purpose": "coverage-guided fuzzing (executes target code)"},
        "hardware": {"mode": "first_success", "auto": False, "purpose": "probe / capture / flash physical targets"},
    },
    "permission_policy": {
        "read": "auto-approved (tool reads the target; writes only inside its own run dir)",
        "write": "explicit approval: --approve <tool> or --approve-class write",
        "hw_read": "explicit approval (energizes a physical target): --approve <tool> or --approve-class hw_read",
        "hw_write": "Frank approval: --frank-ticket <wq id> whose frank_response approves; agents cannot self-approve",
        "network": "BLOCKED (RE isolation rule). No override. Runner also unshares the network namespace for every tool.",
    },
    "category_action_class": {
        "fuzzing": "write", "emulation_user": "write", "emulation_system": "write", "hybrid_emulation": "write",
        "debugging": "write", "dynamic_instrumentation": "write", "exploitation": "write",
        "hardware_flash": "hw_write", "hardware_debug": "hw_read", "hardware_capture": "hw_read",
        "network_mitm": "network", "exploit_database": "network",
    },
    "category_stage": {
        "firmware_extraction": "extract", "android_re": "static", "jvm_re": "static",
        "runtime_decompilation": "runtime_recovery", "binary_disassembly": "static", "binary_decompilation": "static",
        "binary_parsing": "static", "malware_analysis": "capability", "memory_forensics": "forensics",
        "firmware_analysis": "firmware_analysis", "emulation_user": "dynamic", "emulation_system": "dynamic",
        "hybrid_emulation": "dynamic", "debugging": "dynamic", "dynamic_instrumentation": "dynamic",
        "symbolic_execution": "symbolic", "fuzzing": "fuzzing", "hardware_flash": "hardware",
        "hardware_debug": "hardware", "hardware_capture": "hardware", "diffing": "static", "exploitation": "dynamic",
    },
    "usb_hardware_ids": {
        "2207": "rockchip (maskrom/loader)", "0e8d": "mediatek (brom/preloader)", "05c6:9008": "qualcomm EDL",
        "1f3a:efe8": "allwinner FEL", "0403": "FTDI (JTAG/SWD/UART)", "1a86:5512": "CH341A SPI programmer",
        "1a86:7523": "CH340 UART", "0483:3748": "ST-LINK/V2", "0483:374b": "ST-LINK/V2-1", "1366": "SEGGER J-Link",
        "0925:3881": "fx2lafw logic analyzer", "1d50:608c": "fx2lafw logic analyzer", "1d50:6018": "Black Magic Probe",
        "2e8a:000c": "Raspberry Pi debugprobe (CMSIS-DAP)", "303a": "espressif USB JTAG/serial",
    },
}

T = {}  # tool key -> orchestration block


def tool(key, stage, cls, argv=None, stdout=None, limits=None, rw_paths=None, env=None, parse="generic",
         input_member=None, devices=False, note=None, runner="maksoor"):
    blk = {"stage": stage, "action_class": cls, "runner": runner, "parse": parse}
    if argv:
        blk["invocation"] = {"argv": argv, **({"stdout": stdout} if stdout else {}),
                             **({"rw_paths": rw_paths} if rw_paths else {}), **({"env": env} if env else {}),
                             **({"input_member": input_member} if input_member else {}),
                             **({"devices": True} if devices else {})}
    elif devices:
        blk["devices"] = True
    if limits:
        blk["limits"] = limits
    if note:
        blk["note"] = note
    T[key] = blk


# --- extract
tool("unblob", "extract", "read",
     ["/usr/local/bin/unblob", "-e", "{out}/extract", "--report", "{out}/unblob-report.json",
      "--log", "{out}/unblob.log", "-d", "6", "-p", "4", "{target}"],
     limits={"timeout_s": 900}, parse="unblob")
tool("binwalk", "extract", "read",
     ["/usr/bin/binwalk", "-e", "-M", "-d", "4", "-C", "{out}/extract", "-f", "{out}/binwalk.log", "{target}"],
     limits={"timeout_s": 900}, parse="binwalk")
tool("apktool", "extract", "read", ["/usr/bin/apktool", "d", "-f", "-o", "{out}/apktool", "{target}"],
     limits={"timeout_s": 600}, parse="apktool")
tool("gdre-tools", "runtime_recovery", "read",
     ["/usr/local/bin/gdre_tools", "--headless", "--recover={target}", "--output={out}/gdre"],
     limits={"timeout_s": 900}, parse="tree")
tool("uefitool", "extract", "read", note="UEFIExtract not installed on maksoor (registry: lumen/horus): plan-only")
# --- runtime recovery
tool("goresym", "runtime_recovery", "read", ["/usr/local/bin/goresym", "-t", "-d", "-p", "{target}"],
     stdout="goresym.json", parse="goresym")
tool("blutter", "runtime_recovery", "read", ["/usr/bin/python3", "/opt/blutter/blutter.py", "{target}", "{out}/blutter"],
     limits={"timeout_s": 1800, "memory_mb": 8 * GB}, rw_paths=["/opt/blutter"], parse="tree",
     note="first run per Dart version compiles blutter into /opt/blutter/bin (rw-bound)")
tool("hermes-dec", "runtime_recovery", "read",
     ["/home/maksoor/.local/bin/hbc-decompiler", "{input}", "{out}/decompiled.js"],
     input_member="index.android.bundle", parse="tree",
     note="APK targets: runner unpacks assets/index.android.bundle first; HBC targets used directly")
tool("pycdc", "runtime_recovery", "read", ["/usr/local/bin/pycdc", "{target}"], stdout="decompiled.py", parse="tree",
     note="bytecode <= 3.13")
tool("cpp2il", "runtime_recovery", "read", note="needs game dir + global-metadata.dat; plan-only (run by hand)")
# --- static
tool("rizin", "static", "read",
     ["/usr/local/bin/rizin", "-2", "-q", "-c",
      "aa;iIj > info.json;isj > symbols.json;iij > imports.json;aflj > functions.json;izj > strings.json", "{target}"],
     limits={"timeout_s": 600}, parse="rizin")
tool("ghidra", "static", "read",
     ["/opt/ghidra/support/analyzeHeadless", "{out}", "ghidra_proj", "-import", "{target}", "-deleteProject",
      "-log", "{out}/ghidra.log"],
     limits={"timeout_s": 1800, "memory_mb": 8 * GB}, parse="ghidra",
     note="headless import+auto-analysis; interactive work via GhidraMCP / REA")
tool("radare2", "static", "read",
     ["/usr/bin/r2", "-2", "-q", "-c", "aa;iIj > info.json;isj > symbols.json;aflj > functions.json;izj > strings.json",
      "{target}"], limits={"timeout_s": 600}, parse="rizin")
tool("jadx", "static", "read", ["/usr/local/bin/jadx", "-d", "{out}/jadx", "-j", "4", "{target}"],
     limits={"timeout_s": 1200, "memory_mb": 6 * GB}, env={"JAVA_OPTS": "-Xmx4g"}, parse="tree")
tool("droidsaw", "static", "read", ["/home/maksoor/.cargo/bin/droidsaw", "info", "{target}"],
     stdout="droidsaw-info.json", parse="json_generic")
tool("recaf", "static", "read", note="GUI only: plan-only")
tool("lief", "static", "read", note="python API (re-venv); used ad hoc: plan-only")
tool("bindiff", "static", "read", note="needs two BinExports: plan-only")
# --- capability / forensics
tool("capa", "capability", "read", ["/usr/local/bin/capa", "-j", "-q", "{target}"], stdout="capa.json",
     limits={"timeout_s": 900}, parse="capa")
tool("volatility3", "forensics", "read",
     ["/home/maksoor/.local/bin/vol", "-q", "--offline", "-r", "json", "-f", "{target}", "banners.Banners"],
     stdout="vol-banners.json", limits={"timeout_s": 900, "memory_mb": 6 * GB}, parse="volatility")
# --- firmware analysis
tool("emba", "firmware_analysis", "write", runner="lumen",
     note="installed on lumen only and needs root/docker; constraint 'tools run on maksoor' -> plan-only")
# --- dynamic (executes target code: write class)
for k in ("qiling", "qemu-user", "renode", "rr", "pwndbg", "gdb-gef", "avatar2"):
    tool(k, "dynamic", "write", note="executes target code: approval required; interactive, plan-only")
tool("frida", "dynamic", "write", note="needs a sandboxed device/emulator; plan-only")
# --- symbolic (analysis only)
tool("angr", "symbolic", "read", note="interactive via angr-mcp (Lumen) or scripts; plan-only")
tool("triton", "symbolic", "read", note="script-driven; plan-only")
# --- fuzzing
for k in ("afl++", "honggfuzz", "syzkaller"):
    tool(k, "fuzzing", "write", note="harness-specific; approval required; plan-only")
# --- hardware
tool("openocd", "hardware", "hw_read", devices=True, note="JTAG/SWD; reads are energizing, writes (flash) are hw_write")
tool("probe-rs", "hardware", "hw_read", devices=True)
tool("sigrok-cli", "hardware", "hw_read", devices=True)
tool("tio", "hardware", "hw_read", devices=True, runner="lumen")
for k in ("flashrom", "rkdeveloptool", "mtkclient", "edl", "me_cleaner"):
    tool(k, "hardware", "hw_write", devices=True,
         note="can erase/flash a physical device: Frank approval (--frank-ticket) for every invocation")
# --- network tools
tool("bettercap", "dynamic", "network", runner="lumen", note="network tool: BLOCKED by RE isolation rule")
tool("searchsploit", "capability", "network", runner="lumen",
     note="offline DB lookup is fine by hand; the router blocks it because updates hit the network")
tool("pwntools", "dynamic", "write", note="exploit dev library; plan-only")


def merge(reg):
    changed = []
    if reg.get("orchestration") != ORCH:
        reg["orchestration"] = ORCH
        changed.append("orchestration")
    tools = reg.get("tools", {})
    for k, blk in T.items():
        if k not in tools:
            continue
        if tools[k].get("orchestration") != blk:
            tools[k]["orchestration"] = blk
            changed.append(f"tools.{k}.orchestration")
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--registry", default=str(REG))
    a = ap.parse_args()
    p = Path(a.registry)
    reg = json.loads(p.read_text())
    before = {k: json.dumps(v, sort_keys=True) for k, v in reg.items() if k not in ("orchestration", "tools")}
    before_tools = {k: json.dumps({x: y for x, y in v.items() if x != "orchestration"}, sort_keys=True)
                    for k, v in reg.get("tools", {}).items()}
    changed = merge(reg)
    for k, v in before.items():  # guard: only orchestration blocks may differ
        assert json.dumps(reg[k], sort_keys=True) == v, f"merge touched {k}"
    for k, v in before_tools.items():
        assert json.dumps({x: y for x, y in reg["tools"][k].items() if x != "orchestration"}, sort_keys=True) == v, k
    missing = sorted(set(T) - set(reg.get("tools", {})))
    if a.check:
        print(json.dumps({"would_change": changed, "skipped_not_in_registry": missing}))
        sys.exit(1 if changed else 0)
    if not changed:
        print(json.dumps({"changed": [], "skipped_not_in_registry": missing, "note": "already merged"}))
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    bk = BACKUP_DIR / f"wq5200-registry-{time.strftime('%Y%m%d_%H%M%S')}.json"
    shutil.copy2(p, bk)
    tmp = p.with_suffix(".json.wq5200tmp")
    tmp.write_text(json.dumps(reg, indent=2) + "\n")
    json.loads(tmp.read_text())
    tmp.replace(p)
    print(json.dumps({"changed": changed, "backup": str(bk), "skipped_not_in_registry": missing}))


if __name__ == "__main__":
    main()
