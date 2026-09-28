#!/usr/bin/env python3
"""wq#2903: point the two gateway callers still naming mistral-small at live rungs.

  agent/lumen_quota_manager.py  PROVIDER_TO_MODEL["mistral"]: lumen-fleet-mistral's
      model (lumen-agent-2 key, 198 ProxyModelNotFoundError/5d) -> deepseek-v4-flash,
      the consolidation rung FLEET_COST_PLAN.md names for lumen-agent.
  minerva/model_audit.py        AUDIT_MODELS default: the 12h known-answer battery
      (ops-emergency-3 key, 4 prompts x 2 runs/day = 40/5d) drops the retired rung.

Exact-string edits: each target must hold either the old text (patched, after a
.bak.<stamp>_wq2903 copy) or the new text (already done). Anything else aborts
without writing, so a file someone changed since is never clobbered.
  patch_callers.py            apply
  patch_callers.py --check    report only; exit 1 if any target is unpatched
"""
import shutil, sys, time

PATCHES = [
    ("/home/lumen/agent/lumen_quota_manager.py",
     '    "mistral":    "mistral-small",\n',
     '    "mistral":    "deepseek-v4-flash",  # wq#2903: mistral-small left the gateway 2026-08-30\n'),
    ("/home/lumen/minerva/model_audit.py",
     '    "AUDIT_MODELS", "cerebras-llama,cerebras-oss-120b,deepseek-v4-flash,"\n'
     '    "deepseek-v4-pro,mistral-small")\n',
     '    "AUDIT_MODELS", "cerebras-llama,cerebras-oss-120b,deepseek-v4-flash,"\n'
     '    "deepseek-v4-pro")  # wq#2903: mistral-small left the gateway 2026-08-30\n'),
]


def state(path, old, new):
    text = open(path).read()
    if new in text:
        return "patched", text
    if text.count(old) == 1:
        return "unpatched", text
    return "unknown", text


def main(argv):
    check = "--check" in argv
    stamp = time.strftime("%Y%m%d_%H%M%S")
    results = [(p, o, n) + state(p, o, n) for p, o, n in PATCHES]
    bad = [p for p, _, _, s, _ in results if s == "unknown"]
    if bad:
        for p in bad:
            print(f"ABORT {p}: neither the old nor the new text is present; edit by hand")
        return 2
    rc = 0
    for path, old, new, s, text in results:
        if s == "patched":
            print(f"ok       {path}")
        elif check:
            print(f"MISS     {path}")
            rc = 1
        else:
            shutil.copy2(path, f"{path}.bak.{stamp}_wq2903")
            with open(path, "w") as fh:
                fh.write(text.replace(old, new, 1))
            print(f"patched  {path} (backup .bak.{stamp}_wq2903)")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
