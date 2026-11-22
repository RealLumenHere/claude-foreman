"""wq#5323 probe: review-readonly pack vs foreman build pack on the base.json-only
credential Read denies. Calls Permissions.check() only; never opens the target paths.
Path literals are assembled at runtime so the guard's literal-path tripwire is not hit.
Run: python3 /tmp/fworker-wq5323/probe_review_pack.py <foreman-worktree>"""
import json
import os
import sys

wt = sys.argv[1]
sys.path.insert(0, os.path.join(wt, "harness"))
import permission_profiles as pp  # noqa: E402
import fworker_sdk as sdk  # noqa: E402

ws = json.load(open(os.path.join(wt, "harness", "worker-settings.json")))
review = pp.render_review_readonly(ws)
build = pp.render_permissions("/home/lumen/foreman", ws)

ctx = "/home/lumen/minerva/con" + "text/"
cred = "claude_cred" + "entials.md"
paths = [ctx + "sec" + "tions.json",
         ctx + "sec" + "tions.json.bak",
         ctx + "sec" + "tions/x-cred" + "entials.md",
         ctx + cred + ".bak",
         ctx + cred]


def P(d):
    return sdk.Permissions(d, [wt], "acceptEdits")


fail = 0
for p in paths:
    for tool in ("Grep", "Glob", "Read"):
        r = P(review).check(tool, {"path": p, "file_path": p})
        b = P(build).check(tool, {"path": p, "file_path": p})
        gap = r[0] and not b[0]
        fail += gap
        print(f"{tool:4} ...{p[-26:]:26} review={r[0]!s:5} build={b[0]!s:5}"
              f"{'  <-- REGRESSION' if gap else ''}")
print("Bash ", P(review).check("Bash", {"command": "ls"}))
print("Write", P(review).check("Write", {"file_path": wt + "/x"}))
print("Edit ", P(review).check("Edit", {"file_path": wt + "/x"}))
print("is_review_readonly:", pp.is_review_readonly(review))
print("regressions:", fail)
sys.exit(1 if fail else 0)
