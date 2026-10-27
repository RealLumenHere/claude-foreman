#!/usr/bin/env python3
"""Test-to-failure harness for research-prompt-adapter (wq#4677, design §7).

Runs the adapter against a temp inbox and a stub `wq` so NO real ticket is filed.
The stub mimics the two wq behaviours the adapter depends on:
  exit 0  + "added task N [research]"            on a new title
  exit 1  + "DUPLICATE — task N [research/open]" when the title was seen before (non-done)
Run:  python3 tests/test_adapter.py [path-to-adapter]   (default: ../research-prompt-adapter)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADAPTER = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "research-prompt-adapter"

STUB = r'''#!/usr/bin/env python3
import json, os, sys
# argv: add --lane research --pri 3 --by X --title T --detail D --liveness L
args = sys.argv[1:]
title = args[args.index("--title") + 1]
detail = args[args.index("--detail") + 1]
db = os.environ["STUB_DB"]
d = json.load(open(db)) if os.path.exists(db) else {"next": 100, "titles": {}, "log": []}
if os.environ.get("STUB_FAIL") == "1":
    print("REFUSED: lane 'research' simulated outage"); sys.exit(1)
if title in d["titles"]:
    print(f"DUPLICATE — task {d['titles'][title]} [research/open] already has this title:\n  {title}\nUse --force to add anyway.")
    json.dump(d, open(db, "w")); sys.exit(1)
tid = d["next"]; d["next"] += 1; d["titles"][title] = tid
live = args[args.index("--liveness") + 1] if "--liveness" in args else ""
d["log"].append({"id": tid, "title": title, "detail_len": len(detail), "detail_head": detail[:80], "liveness": live})
json.dump(d, open(db, "w"))
print(f"added task {tid} [research]")
'''

fails = 0


def check(cond, msg):
    global fails
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


def run(env, *flags):
    p = subprocess.run([sys.executable, str(ADAPTER), *flags], capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


def summary(out):
    line = [l for l in out.splitlines() if "summary:" in l][-1]
    return dict(kv.split("=") for kv in line.split("summary:")[1].split() if "=" in kv and not kv.startswith("inbox"))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="rpa-test-"))
    inbox = tmp / "inbox"; inbox.mkdir()
    stub = tmp / "wq"; stub.write_text(STUB); stub.chmod(0o755)
    state = tmp / "state.json"
    db = tmp / "stub.json"
    fres = tmp / "foreman-research"; fres.mkdir()
    sres = tmp / "scratch-research"; sres.mkdir()
    env = dict(os.environ, RPA_INBOX=str(inbox), RPA_STATE=str(state), RPA_WQ=str(stub), STUB_DB=str(db),
               RPA_FOREMAN_RESEARCH=str(fres), RPA_SCRATCH_RESEARCH=str(sres))
    try:
        # 1. five distinct prompts -> five tickets
        for i in range(5):
            (inbox / f"p{i}.txt").write_text(f"RESEARCH PROMPT: subject number {i} for the fleet\nbody line {i}\n")
        rc, out = run(env); s = summary(out)
        check(rc == 0 and s["filed"] == "5", f"T1 five prompts filed (rc={rc} {s})")
        q = sorted(p.name for p in (inbox / "queued").glob("*.txt"))
        check(len(q) == 5 and all(n.split("_")[0].isdigit() for n in q), f"T1 queued names carry wq id: {q}")
        check(len(json.load(open(state))["by_sha"]) == 5, "T1 ledger has five shas")
        titles = [e["title"] for e in json.load(open(db))["log"]]
        check(titles[0] == "RESEARCH: subject number 0 for the fleet", f"T1 title derivation: {titles[0]!r}")

        # 2. second run: nothing re-filed
        rc, out = run(env); s = summary(out)
        check(s["filed"] == "0" and s["dup"] == "0", f"T2 second run idle: {s}")

        # 3. duplicate body under a new name -> no ticket, joins existing id
        shutil.copy(inbox / "queued" / q[0], inbox / "renamed-copy.txt")
        before = json.load(open(db))["next"]
        rc, out = run(env); s = summary(out)
        after = json.load(open(db))["next"]
        joined = list((inbox / "queued").glob("*_renamed-copy.txt"))
        check(before == after and s["dup"] == "1" and joined and joined[0].name.startswith(q[0].split("_")[0] + "_"),
              f"T3 dup body -> no ticket, queued as {joined[0].name if joined else None}")

        # 4. empty + whitespace-only -> expired, no wq call
        (inbox / "empty.txt").write_text("")
        (inbox / "blank.txt").write_text(" \n\t\n")
        before = json.load(open(db))["next"]
        rc, out = run(env); s = summary(out)
        exp = sorted(p.name for p in (inbox / "expired").glob("*"))
        check(s["expired"] == "2" and json.load(open(db))["next"] == before and "empty_empty.txt" in exp and "empty_blank.txt" in exp,
              f"T4 empty files expired, no ticket: {exp}")

        # 5. 20KB file -> one ticket, head + path marker, body not inlined
        big = "RESEARCH PROMPT: twenty kilobyte prompt\n" + ("x" * 99 + "\n") * 205
        (inbox / "big.txt").write_text(big)
        rc, out = run(env); s = summary(out)
        last = json.load(open(db))["log"][-1]
        check(s["filed"] == "1" and last["detail_len"] < 2600 and "twenty kilobyte" in last["detail_head"],
              f"T5 20KB -> one ticket, detail {last['detail_len']}B (<2600), not inlined")

        # 6. ledger lost + wq says DUPLICATE -> no new ticket, file still queued, ledger rebuilt
        state.unlink()
        (inbox / "p0-again.txt").write_text("RESEARCH PROMPT: subject number 0 for the fleet\nbody line 0 but edited\n")
        before = json.load(open(db))["next"]
        rc, out = run(env); s = summary(out)
        check(json.load(open(db))["next"] == before and s["dup"] == "1" and list((inbox / "queued").glob("100_p0-again.txt"))
              and len(json.load(open(state))["by_sha"]) == 1,
              f"T6 lost ledger + wq DUPLICATE -> joined wq#100, ledger rebuilt: {s}")

        # 7. stale file -> expired/stale
        st = inbox / "old.txt"; st.write_text("RESEARCH PROMPT: ancient prompt nobody asked for\n")
        old = time.time() - 40 * 86400; os.utime(st, (old, old))
        rc, out = run(env); s = summary(out)
        check(s["expired"] == "1" and (inbox / "expired" / "stale_old.txt").exists(), f"T7 40-day-old -> expired/stale: {s}")

        # 8. per-run cap
        for i in range(15):
            (inbox / f"burst{i:02d}.txt").write_text(f"RESEARCH PROMPT: burst prompt number {i} of fifteen\n")
        rc, out = run(env); s = summary(out)
        left = len(list(inbox.glob("burst*.txt")))
        check(s["filed"] == "10" and s["deferred"] == "5" and left == 5, f"T8 cap 10/run: {s}, {left} left")
        rc, out = run(env); s = summary(out)
        check(s["filed"] == "5" and len(list(inbox.glob("burst*.txt"))) == 0, f"T8b next tick drains the rest: {s}")

        # 9. wq outage -> file stays, failed=1, exit 0
        (inbox / "outage.txt").write_text("RESEARCH PROMPT: filed during a wq outage\n")
        rc, out = run(dict(env, STUB_FAIL="1")); s = summary(out)
        check(rc == 0 and s["failed"] == "1" and (inbox / "outage.txt").exists(), f"T9 wq refusal keeps the file: {s}")
        rc, out = run(env); s = summary(out)
        check(s["filed"] == "1" and not (inbox / "outage.txt").exists(), "T9b retried on the next tick")

        # 10. lock held -> locked, nothing touched
        (inbox / "locked.txt").write_text("RESEARCH PROMPT: should not file while locked\n")
        import fcntl
        lk = open(str(state) + ".lock", "a+"); fcntl.flock(lk, fcntl.LOCK_EX)
        rc, out = run(env)
        lk.close()
        check(rc == 0 and "summary: locked" in out and (inbox / "locked.txt").exists(), "T10 lock held -> no-op")

        # 11. intel_* shape: Title: line wins over the boilerplate first line
        (inbox / "intel_x_abc123.txt").write_text(
            "Research the following topic for a technical homelab/AI audience.\nSource: HF Blog\nTitle: Run fast, private agents on-device\nURL: https://example\n")
        rc, out = run(env)
        titles = [e["title"] for e in json.load(open(db))["log"]]
        check("RESEARCH: Run fast, private agents on-device" in titles, f"T11 Title: line beats boilerplate line 1 ({titles[-2:]})")

        # 12. DELIVERABLE: first line -> subject from the next line
        (inbox / "twn4-like.txt").write_text("DELIVERABLE: /home/lumen/scratch/research/x.md\nTWN4 MultiTech 3 Linux reflash runbook\nbody\n")
        rc, out = run(env)
        check(json.load(open(db))["log"][-1]["title"] == "RESEARCH: TWN4 MultiTech 3 Linux reflash runbook",
              "T12 DELIVERABLE: line skipped for the title")

        # 14. DELIVERABLE: path wins the liveness probe; stem fallback otherwise
        last = json.load(open(db))["log"][-1]
        check(last["liveness"] == f"test -f /home/lumen/scratch/research/x.md || test -f {fres}/x.md",
              f"T14 DELIVERABLE: path is the probe (dual): {last['liveness']!r}")
        plain = [e for e in json.load(open(db))["log"] if e["title"].startswith("RESEARCH: subject number 1")][0]
        check(plain["liveness"] == f"test -f {sres}/p1.md || test -f {fres}/p1.md",
              f"T14b stem fallback probe (dual): {plain['liveness']!r}")

        # 15. mirror pass: a deliverable landed under foreman/research by PR is copied into
        #     scratch/research on the next tick; never overwritten once present
        (inbox / "landed.txt").write_text(f"DELIVERABLE: {sres}/landed-doc.md\nLanded by an fworker\n")
        rc, out = run(env)
        (fres / "landed-doc.md").write_text("v1 from PR\n")
        rc, out = run(env); s15 = summary(out)
        check(s15["mirrored"] == "1" and (sres / "landed-doc.md").read_text() == "v1 from PR\n",
              f"T15 landed deliverable mirrored into scratch/research: {s15}")
        (fres / "landed-doc.md").write_text("v2 from PR\n")
        rc, out = run(env); s15 = summary(out)
        check(s15["mirrored"] == "0" and (sres / "landed-doc.md").read_text() == "v1 from PR\n",
              "T15b existing scratch copy never overwritten")
        # 16. stem-fallback prompt (no DELIVERABLE:) mirrors by <stem>.md too
        (inbox / "stemmed.txt").write_text("RESEARCH PROMPT: stem based deliverable mirroring\n")
        rc, out = run(env)
        (fres / "stemmed.md").write_text("stem doc\n")
        rc, out = run(env); s16 = summary(out)
        check(s16["mirrored"] == "1" and (sres / "stemmed.md").exists(), f"T16 stem fallback mirrored: {s16}")

        # 13. dry-run touches nothing
        (inbox / "dry.txt").write_text("RESEARCH PROMPT: dry run candidate\n")
        before = json.load(open(db))["next"]
        rc, out = run(env, "--dry-run")
        check("DRY would file dry.txt" in out and (inbox / "dry.txt").exists() and json.load(open(db))["next"] == before,
              "T13 --dry-run files nothing, moves nothing")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAIL'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
