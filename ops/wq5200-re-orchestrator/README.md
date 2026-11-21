# wq#5200 Limpet Capability Router (`re-orchestrate`)

An agent hands over a target (or only a description) and gets back a structured investigation plan,
then optionally runs it: tool chains chosen from the wq#5187 capability registry, permission-gated,
resource-limited on Maksoor, normalized into evidence envelopes on Lumen.

```
agent ──> re-orchestrate (Lumen)
            ├─ re-fingerprint      format/arch/os/vendor/embedded formats (nested, recursive into gzip/xz/lzma/bz2/zstd)
            ├─ build_plan          registry routing.rules + augments + evidence heuristics -> ranked chains + stages
            ├─ gate                read=auto · write/hw_read=--approve · hw_write=Frank wq ticket · network=BLOCKED
            └─ Runner (ssh maksoor) re-exec: systemd --user scope (MemoryMax/TasksMax) + bwrap (no net, ro target, ro root)
                                    watchdog: timeout + disk budget; partial output kept on kill
          <── envelopes  /home/lumen/re/evidence/<project>/<analysis_id>/<tool>.json  + raw/ + gate.jsonl
              index.jsonl · lumen_state re-<project>.last_analysis · Limpet _meta.evidence (ev_<sha256>) per run
```

## Use

```bash
re-orchestrate plan /path/to/dump.bin --human                 # plan only, nothing runs
re-orchestrate plan --describe "unknown firmware dump from a Rockchip device"
re-orchestrate run  /path/to/dump.bin --project rk3399-scanner  # auto stages: extract, runtime_recovery, static, capability, forensics
re-orchestrate run  maksoor:/data/re-work/.../carved.elf --project rk3399-scanner   # target already on the runner
re-orchestrate run  app.apk --project x --tools qiling --approve qiling            # opt-in, write class
re-orchestrate run  dump.bin --project x --tools flashrom --frank-ticket 6001 --argv '["/usr/sbin/flashrom",...]'
re-orchestrate search <sha|project|format|vendor>
re-orchestrate runner-check | runner-deploy
```

Every command prints JSON (`--human` for a summary). Exit 0 ok · 2 usage · 3 runner unreachable · 4 every requested tool gated.

## Pieces

| file | lives | role |
|---|---|---|
| `re-fingerprint` | lumen-bin → `/home/lumen/bin`, copy on maksoor | target fingerprinter (stdlib) |
| `re-orchestrate` | lumen-bin → `/home/lumen/bin` | router, gate, executor, normalizers, evidence |
| `re-exec-runner` | lumen-bin; deployed to `maksoor:~/.local/lib/re-orchestrate/re-exec` | per-tool execution envelope |
| `registry_merge_wq5200.py` | this bundle | merges `orchestration` blocks into the registry (backup, idempotent, `--check`) |
| `limpet-manifest-snippet.json` | this bundle | `cli_tools` entries for a Limpet meta-server that can reach maksoor |

Registry: per-tool `tools[k].orchestration` = stage, action_class, runner, invocation (argv template with
`{target}` `{out}` `{input}`), limits, parse. Top-level `orchestration` = defaults (300 s / 4 GiB / 10 GiB),
stage order + modes, permission policy, category maps, USB IDs for hardware-on-runner detection.
`registry_merge_wq5187.py` was patched to carry unknown top-level keys forward, so a 5187 regen keeps these.
If the block is ever missing, re-orchestrate falls back to built-in defaults (`orchestration_source` says which).

Stages: `first_success` stages try candidates in score order and stop at the first ok/partial (tool failure,
timeout, kill, missing binary -> next). `all` stages run every allowed candidate. Non-auto stages (dynamic,
symbolic, fuzzing, firmware_analysis, hardware) are listed as opt-in next steps with their gate preview.

Tools with no headless invocation (qiling, angr, afl++, frida, openocd, flashrom, ...) are plan-only: listed,
classified, gated, never auto-run. `--argv` lets a caller supply one for a single `--tools` entry; it still
passes the gate (hw_write still needs Frank).

## Install / verify

```bash
bash /home/lumen/foreman/ops/wq5200-re-orchestrator/install.sh           # after the lumen-bin PR lands
bash /home/lumen/foreman/ops/wq5200-re-orchestrator/install.sh --verify  # read-only
```

## Verified 2026-11-21 (live, maksoor)

OOM kill at `--mem 128` · timeout at 5 s with partial file kept · disk kill at 20 MiB · network unreachable
inside the sandbox · target write refused (read-only bind) · bettercap blocked · qiling needs approval ·
flashrom needs a Frank ticket (and wq#5200, which has no frank_response, is refused) · unblob->binwalk fallback
under a disk cap · RK3399 kernel.img -> unblob extract -> carved ELF next step -> rizin on the runner-side path ·
APK -> apktool (manifest/permissions) -> jadx (13,295 files, 20 class errors = partial) · ARM ELF -> rizin
(51 call edges, 1,295 strings), capa skipped on arch · lumen_state ingest. Unit tests: lumen-bin
`tests/test_re_orchestrate.py`.
