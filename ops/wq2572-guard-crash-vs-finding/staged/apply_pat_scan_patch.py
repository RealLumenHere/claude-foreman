#!/usr/bin/env python3
"""wq#2572 deploy helper: apply the pat-scan self-alert + crash-isolation patch to the
live /home/lumen/bin/wq523_pat_residue.py via exact anchored str.replace (never a blind
overwrite) -- fails loudly if either anchor is not found verbatim (e.g. the live file
already changed), rather than silently producing a corrupted script. See
ops/wq2572-guard-crash-vs-finding/DESIGN.md and staged/wq523_pat_residue.diff (same
change, unified-diff form, for review) -- this script is what actually applies it.
"""
import sys

TARGET = "/home/lumen/bin/wq523_pat_residue.py"

ALERT_OLD = '''    for f, mode, why in critical:
        print(f"  CRITICAL {mode} {f}  [{why}]")
    if not critical:
        print("  no stray file is world-readable or holds a live token")'''

ALERT_NEW = '''    for f, mode, why in critical:
        print(f"  CRITICAL {mode} {f}  [{why}]")
    if critical:
        # wq#2572: self-alert independent of systemd state -- see DESIGN.md in
        # ops/wq2572-guard-crash-vs-finding for why this can no longer rely on
        # OnFailure=/lumen-failed-units once this unit moves to SuccessExitStatus=1.
        _key = hashlib.sha256(",".join(sorted(f for f, _, _ in critical)).encode()).hexdigest()[:16]
        _title = f"pat-scan: {len(critical)} CRITICAL token residue file(s)"
        _detail = "; ".join(f"{f} [{why}]" for f, _, why in critical)[:1500]
        try:
            subprocess.run(["/home/lumen/bin/aq", "add", "pat-scan", _key, _title, _detail,
                            "--owner", "claude"], capture_output=True, text=True, timeout=20)
        except Exception:
            pass
        try:
            subprocess.run(["/home/lumen/bin/ntfy-send", "-t", "lumen-alerts", "-p", "high",
                            "-T", _title, _detail], capture_output=True, text=True, timeout=15)
        except Exception:
            pass
    if not critical:
        print("  no stray file is world-readable or holds a live token")'''

MAIN_OLD = '''def main():
    if "--scan" in sys.argv:
        sys.exit(cmd_scan())
    if "--alert" in sys.argv:
        sys.exit(cmd_alert())
    index, oversize = build_index()'''

MAIN_NEW = '''def main():
    # wq#2572: an unhandled exception here used to fall through to Python's default
    # exit code 1 -- IDENTICAL to cmd_alert()'s "found a CRITICAL" exit 1, so a crash
    # and a real finding were indistinguishable from the outside. Give a crash its
    # own code so SuccessExitStatus=1 (finding only) never also swallows a real
    # failure. See ops/wq2572-guard-crash-vs-finding/DESIGN.md.
    try:
        if "--scan" in sys.argv:
            sys.exit(cmd_scan())
        if "--alert" in sys.argv:
            sys.exit(cmd_alert())
    except SystemExit:
        raise
    except Exception as e:
        print(f"wq523_pat_residue: internal error: {e}", file=sys.stderr)
        sys.exit(2)
    index, oversize = build_index()'''


def main():
    with open(TARGET) as fh:
        text = fh.read()
    for label, old, new in (("cmd_alert self-alert", ALERT_OLD, ALERT_NEW),
                             ("main crash isolation", MAIN_OLD, MAIN_NEW)):
        if text.count(old) != 1:
            print(f"REFUSING: anchor for {label!r} not found exactly once "
                  f"(found {text.count(old)}) -- live file has diverged, re-derive the patch",
                  file=sys.stderr)
            sys.exit(1)
        text = text.replace(old, new)
    with open(TARGET, "w") as fh:
        fh.write(text)
    print(f"patched {TARGET}")


if __name__ == "__main__":
    main()
