# wq#4821 — verify pass on wq#4815 (install.sh pytest gate as lumen, not root)

Verifier: claude-sonnet-5@high, fresh instance, same model as author. Verdict: **CONFIRM**.

## What wq#4815 claims

`ops/wq3354-fleet-selfheal/install.sh:70` wraps the pytest gate (previously plain
`python3 -m pytest -q "$SRC/tests"`, run as root) in `runuser -u lumen --`, because
root's `python3` has no `HOME` and can't see lumen's `--user` site-packages where
`ruamel.yaml` lives (imported by `selfheal_merge.py`), so the gate could never pass
as root and `install.sh` (non-`--verify` path) could never complete a real privileged
apply.

## Adversarial checks run

1. **Root-cause repro, independent of wq#4815's own report.** Ran the *old* (unwrapped)
   invocation directly as root via `lumen-run start --root --name wq4821rootpytest --
   python3 -m pytest -q /home/lumen/foreman/ops/wq3354-fleet-selfheal/tests`: collection
   error, `ModuleNotFoundError: No module named 'ruamel'`
   (`selfheal_merge.py:35 from ruamel.yaml import YAML`), exit 2. Confirms the claimed
   failure mode as root, cold — not just trusting wq#4815's own writeup.

2. **Fix repro.** Ran the *new* invocation directly as root via `lumen-run start --root
   --name wq4821fixedpytest -- runuser -u lumen -- python3 -m pytest -q
   /home/lumen/foreman/ops/wq3354-fleet-selfheal/tests`: `79 passed, 1 skipped, 1 warning
   in 3.01s`, exit 0. The exact wrapper installed at `install.sh:70` resolves the failure.

3. **Completeness of the "only selfheal_merge.py needs lumen's env" claim.** Grepped
   top-level imports of every file in `install.sh`'s `CODE[]` array
   (`selfheal_common.py sh_guards.py sh_crashloop.py sh_disk.py sh_providers.py
   sh_nodes.py sh_ollama.py leakwatch.py exposure_census.py selfheal_merge.py` plus
   `selfheal_sweep.py`): every one is stdlib-only (`argparse`, `os`, `json`, `re`,
   `urllib`, `ctypes`, `grp`, `pwd`, `select`, `struct`, `datetime`, `fnmatch`,
   `subprocess`, `shutil`, `time`, `sys`, intra-package imports) except
   `selfheal_merge.py`, which imports both `yaml` (PyYAML) and `ruamel.yaml`. So
   wrapping only the pytest gate (which collects the whole `tests/` tree, including
   `test_seed_merge.py` which imports `selfheal_merge`) is sufficient — no other
   root-run step in the script (`selfheal_sweep.py --dry-run` at line 88, run
   unwrapped as root) needs a third-party import it doesn't already run unwrapped
   today. No scope gap found.

4. **Pattern consistency.** The fix mirrors the pre-existing `runuser -u lumen --`
   wrap around `selfheal_merge.py` at `install.sh:79-80`, for the same `ruamel`
   reason. Not a new pattern introduced for this fix.

5. **Gating unaffected.** The wrapped line sits under `[ "$(id -u)" = 0 ] || exit 1`
   (`install.sh:65`), so this only ever executes when already running as root; no
   change to when the script demands root.

6. **Live liveness probe** (per ticket's `Liveness: ops_verified(wq3354-fleet-selfheal)`):
   `lumen-run start --name wq4821verify -- bash
   /home/lumen/foreman/ops/wq3354-fleet-selfheal/install.sh --verify` → unit exit 0,
   journal shows `INFO live-only: nodes.notes.neith` then `VERIFY OK`. Live target
   matches the shipped bundle.

## Nothing found to contradict

No edge case broke: the redirect (`>/dev/null 2>&1`) still wraps the whole `runuser`
invocation so pytest's exit code (not runuser's) still propagates; `runuser` is on the
unit's `PATH` (confirmed by the successful fixed-pytest run above); no other CODE[]
file needed the same treatment. wq#4815's fix is real, correctly scoped, and the live
bundle verifies clean.

## Evidence

See `evidence.json`: AC1 → `ops/wq3354-fleet-selfheal/install.sh:70` (the `runuser -u
lumen --` wrap wq#4815 added, present on this branch).
