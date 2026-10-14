# wq#3888 — verify-pass of wq#3875 Fuzzware MMIO eval (PR#1858)

**Verdict: CONFIRMED.** wq#3875's doc addition (`TOOL_REGISTRY.md:144-221`, merged
`b760fd28`/`e2f18256`) is a research/evaluation deliverable, not code — there is no
runnable acceptance probe in the code-change sense. "Running its probe" here means
independently reproducing every falsifiable claim against the project's own primary
sources (the Fuzzware repos themselves), not trusting the doc's paraphrase. Every
specific, checkable claim reproduced exactly. No findings.

## What wq#3875 claimed

NO-GO for the ticket's proposed `unblob extract → Fuzzware MMIO model → Qiling
emulation` pipeline. Reasoning: Fuzzware targets bare ARM Cortex-M3/M4 firmware
blobs with no OS/filesystem; `unblob` extracts filesystems out of Linux-class
embedded firmware. The two tools solve for different artifact classes, so chaining
them as scoped doesn't hold together. Supporting claims: Docker is the documented
install path; native install needs two venvs because the angr 8.19.10.30 modeling
dependency caps Python at <3.10; Fuzzware is CLI-only (no stable Python API);
output is a `config.yml` keyed by `pc_<PC>_mmio_<addr>` site with
`constant`/`bitextract`/`passthrough` model types; it plugs Unicorn+AFL/AFL++, not
Qiling, and ships no Qiling adapter; `re-tools-analysis.md` (the ticket's own cited
ref) was not found anywhere in the worktree or any `--add-dir` root.

## What I independently reproduced

- **Repo/paper identity**: `https://github.com/fuzzware-fuzzer/fuzzware`, USENIX
  Security '22 paper, as cited. Confirmed via direct fetch of
  `raw.githubusercontent.com/fuzzware-fuzzer/fuzzware/master/README.md`.
- **Core scope quote**: README line 1 — "configure the memory ranges of an ARM
  Cortex-M3 / M4 firmware image, and start emulating / fuzzing the target without
  full device emulation" — matches the doc's "ARM Cortex-M3/M4" and "without full
  device emulation" phrasing verbatim, not a loose paraphrase.
- **Install path**: README Quick Start — `./build_docker.sh` then `./run_docker.sh
  examples fuzzware pipeline ...` — matches exactly. Native path —
  `./install_local.sh` sets up `fuzzware` and `fuzzware-modeling` venvs, the second
  because "angr is one of its dependencies" — matches.
- **angr/Python pin**: README §"Too Recent Python Version (>=3.10)" — "We based our
  MMIO modeling component on angr version 8.19.10.30. We learned... this version of
  angr only supports Python versions lower than 3.10... environment variable
  `MODELING_VENV_PYTHON3`" — the doc's quote and the `MODELING_VENV_PYTHON3` var
  name are both exact, not invented.
- **"Future work" / architecture coupling**: README's hacking-ideas list — item 1
  "Upgrade angr version... to support Python in version >=3.10"; item 2
  "Architecture Independence: Currently, the Fuzzware code is rather tighly [sic]
  coupled..." — the doc's "tightly coupled with ARM/Cortex-M" and "upgrade angr...
  to support Python >=3.10 ... still open" claims both check out (doc silently
  fixes the README's own typo "tighly"→"tightly", a reasonable correction, not a
  misquote).
- **Unicorn/AFL binding, no Qiling**: README line 43-44 — "Fuzzware works by
  plugging an instruction set emulator (currently: Unicorn Engine) to a fuzzer
  (currently: afl / AFL++)". Grepped the full README for "qiling" — zero hits. The
  doc's "targets bare Unicorn directly, not Qiling, and ships no Qiling adapter" is
  a correct inference from an absence, not a fabricated claim.
- **CLI-only surface**: README — "`$ fuzzware -h`, and `$ fuzzware <util_name> -h`"
  — no Python import/API documented anywhere in the README. Matches "there isn't a
  clean importable one... CLI orchestrating three components."
- **config.yml MMIO model format — the most specific, most falsifiable claim**:
  fetched `raw.githubusercontent.com/fuzzware-fuzzer/fuzzware-emulator/master/README_config.yml`
  directly via `curl` (fleet-search's HTML-extractor choked on the pure-YAML file,
  returning "fetched OK but extracted no content" despite real `http:200` bytes —
  worth noting as a fleet-search gap, not a doc defect). The raw file's
  `mmio_models:` block (lines 280-329) contains exactly `constant:`, `bitextract:`,
  `passthrough:` keys, each keyed by entries of the form
  `pc_00000e98_mmio_4000c014:` etc. — byte-for-byte match to the doc's claimed
  format, including the PC-qualified site-keying point the doc leans on for the
  "Qiling integration isn't drop-in" reasoning.
- **`re-tools-analysis.md` absence**: searched every accessible root —
  `/home/lumen/worktrees/wq3888-a1`, `/home/lumen/foreman` (full repo, not just the
  worktree), `/home/lumen/kb`, `/home/lumen/agents/work-queue`, `/home/lumen/mailbot`,
  `/home/lumen/pokemon`, `/home/lumen/bin`, `/home/lumen/sentinel` — no match
  anywhere. The doc's "not found" claim holds.
- **Branch history sanity check**: two branches exist for wq#3875
  (`feat/wq3875-...-a1` local, `-a2` on origin). `-a2`'s tip is the merged commit
  `e2f18256` with a clean (empty) diff against `main` — the current merged state is
  the final one, not a stale/competing attempt. `-a1` is an abandoned worktree that
  never committed anything of its own (tip predates wq#3875 entirely) — not a
  second draft that lost content.
- **Internal consistency**: `TOOL_REGISTRY.md` has exactly one `## Fuzzware` heading
  (`grep -n "^## "`), formatted consistently with the adjacent Pretender (wq#3877)
  NO-GO entry — no duplicate or conflicting verdict elsewhere in the file.

## Judgment call not independently re-litigated

The doc's architectural verdict — that a bare Cortex-M MCU blob "has no embedded
filesystem for unblob to carve out in the first place" — is a reasonable reading of
the two tools' documented scopes and is explicitly hedged ("nothing in LIMPET's
current firmware intake produces the bare Cortex-M blobs Fuzzware is built for... if
a bare-metal MCU target shows up... worth a follow-up eval"). A combined
AP+microcontroller firmware image where unblob's nested extraction surfaces an
embedded Cortex-M sub-blob is a theoretical counter-scenario the doc doesn't
enumerate, but the doc's claim is about current LIMPET intake, which I have no
evidence to confirm or deny from this worktree, and the hedge already covers the
case if it ever arises. Not a contradiction — a known-unknown the doc already
flags.

## Why this doesn't escalate

No reproduced claim diverged from its primary source. The one near-miss (fleet-search
failing to extract the pure-YAML `README_config.yml`) was worked around with a direct
`curl` and confirmed the doc's most specific, most falsifiable claim exactly, so it
reflects a tooling gap, not a doc defect. CONFIRMED.
