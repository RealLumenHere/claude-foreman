# wq#4156 — verify-pass on wq#4143 (LIMPET: port iothackbot jtagprobe OPEN/LOCKED/DEAD classifier)

PR `claude-foreman#2100` (http://100.121.17.5:3000/lumen/claude-foreman/pulls/2100),
commit `d7f05ae1` on `main` (branch `feat/wq4143-limpet-mine-iothackbot-jtagprobe-open-lo-a1`).
5 files / 481 insertions, scoped to `plugins/limpet-plugin-openocd-mcp/`
(`mcp/classify_access.py` new, `tests/test_classify_access.py` new, `README.md`,
`limpet-plugin.toml`, `LICENSE` updated). Author: claude-sonnet-5 (fworker-wq4143,
one dispatch). Verifier: claude-sonnet-5@high (fresh instance, wq#4156).

## Claim under test

The PR adds `classify_access`, a pre-energize OPEN/LOCKED/DEAD debug-access classifier,
and declares it in both `README.md` ("Ungated: ... classify_access") and
`limpet-plugin.toml`'s `[mcp.tools_energize]` table (`classify_access = false`) as a
**read-only, autonomous, no-confirm-needed** recon tool — explicitly contrasted in the
same README/toml against the plugin's gated verbs (`flash_write`, `flash_erase`, `halt`,
`reset`, `step`, `register_write`, `memory_write`). The module docstring states it is
"read-only recon run before an operator decides whether to energize destructive tools
... against the target."

## What I checked

1. **Diff cold**: `git show --stat d7f05ae1` — matches the PR's claimed file list exactly
   (5 files, 481 insertions, no pre-existing file touched outside this plugin dir).
2. **Ran the probe + full suite**: `python3 -m pytest tests/test_classify_access.py -q`
   in `plugins/limpet-plugin-openocd-mcp/` → `12 passed`. All three verdict paths
   (OPEN/LOCKED/DEAD) are covered at the `classify_dp_access()` unit level, plus
   `decode_idcode`, `looks_like_vector_table`, and one fake-Tcl-server end-to-end run for
   OPEN and one for DEAD-on-unreachable. The claimed test coverage is real and the suite
   is green.
3. **Checked the ported logic for self-consistency** (`classify_access.py:123-149`):
   the four-way decision tree is internally sound — the final `return "DEAD",
   "Indeterminate..."` at line 149 is dead code (every path above it already required
   `dp_responds` to be `True`, having returned `DEAD` at line 133 otherwise), but it's
   unreachable-as-a-safety-net dead code, not a logic error that changes any verdict.
4. **Wire-protocol parity**: `OpenOCDProbe._send`/`scan_chain_idcodes`/`halt_and_read`
   (`classify_access.py:186-237`) use the same command+`0x1a`-terminator framing and the
   same `scan_chain` column layout (`fields[3]` = IdCode hex) as the already-verified
   `ops/wq3881-openocd-mcp/openocd_client.py:207-250` (`reports/wq3892_verify_wq3881_openocd_mcp.md`).
   This part of the "same wire protocol" claim holds.
5. **Tried to break the one thing the PR's own framing rests on**: that `classify_access`
   is safe to leave ungated because it is read-only / non-destructive, matching this same
   plugin's own definition of that category ("Read-only and passive verbs (enumerate,
   identify, capture) stay fully autonomous" — `docs/plugin-spec.md:310-311`; the project's
   energize axis models risk "bounded by the device under test" — `docs/plugin-spec.md:371`).

   `classify_access()` → `OpenOCDProbe.halt_and_read()` (`classify_access.py:216-237`)
   unconditionally issues a raw `halt` command over the Tcl RPC socket whenever
   `dp_responds` is true (`classify_access.py:271-277`), with no operator confirmation of
   any kind. `halt` is not a passive verb — it is the exact verb this same plugin's own
   manifest marks as destructive and gated two sections above:
   `[mcp.tools_energize]` in `limpet-plugin.toml:65` sets `halt = true` ("Gated" in the
   README), while line 79 sets `classify_access = false` ("Ungated"). The code's own
   docstring for `halt_and_read` admits the side effect in plain language
   (`classify_access.py:217-219`): *"the probe does not resume the core; callers that
   need the target running again should issue `resume` themselves under the normal
   energize gate."* So running the "ungated, autonomous, no-confirm" tool silently stops
   a live target's execution and leaves it halted — the operator only finds out, and only
   gets to decide, *after* the fact, when they notice the target is dead and go looking
   for why. Recovering from that (`resume`) requires the exact energize-confirm this tool
   bypassed to get into the halted state in the first place.

   I confirmed this is not a hypothetical code path: the test suite itself exercises it
   — `test_classify_access_end_to_end_open_via_fake_tcl_server`
   (`tests/test_classify_access.py:123-139`) asserts `result["evidence"]["halted"] is
   True` after calling `ca.classify_access(...)`, i.e. the test's own assertion confirms
   the "ungated" call leaves the (fake) target halted on every successful run, not just
   on a rare error path.
6. **Checked whether gate enforcement exists yet to bound the blast radius**: grepped
   `limpet/*.py` for `energize`/`tools_energize` — no broker/daemon code in this repo
   currently reads or enforces `[mcp.tools_energize]` at invocation time (only
   `limpet/registry_index.py:287-305,328-331` validates/records the separate,
   externally-mirrored `[registry].tool_energize` table for registry-listing purposes).
   So today this is a latent manifest/documentation defect rather than something
   presently exploitable through a live broker — but the whole point of a manifest
   declaration is to be correct *before* the broker that trusts it is wired up
   (`docs/plugin-spec.md:350-368` describes exactly this `[mcp.tools_energize]` block as
   the manifest-level source of truth the registry index mirrors). Shipping it wrong now
   means the first broker that trusts this plugin's manifest inherits the bug for free.
7. **License-provenance claim** (`LICENSE`, `classify_access.py:4-6`): the PR adds a
   copyright line attributing the ported algorithm to "iothackbot contributors... MIT
   License" from `BrownFineSecurity/iothackbot`. I attempted to verify this independently
   via `fleet-search web` and `fleet-search github` — both backends are currently down
   (`web`: all SearXNG engines rate-limited/CAPTCHA'd, no `BRAVE_API_KEY` fallback;
   `github`: 401, no usable token in any configured location). Logged as ISSUE-LOG row
   1401. No vendored copy of upstream `jtagprobe_core.py` exists anywhere in this repo to
   diff against either. I could not confirm or deny the license/porting-fidelity claim
   from available evidence — flagging it as unverified, not as a finding in either
   direction, and it is not the basis of the verdict below.

## Verdict

**CONTRADICT.** The ported classification algorithm is logically sound, the wire
protocol matches the already-verified reference client, and the claimed test coverage
(12/12) is real. But the PR's central safety claim — that `classify_access` is a
read-only, autonomous, ungated recon tool like `jtag_scan`/`identify_chip` — is false:
it performs `halt`, the identical verb this same plugin's manifest treats as gated and
destructive everywhere else, and leaves the target halted with no resume, no
confirmation, and no warning until the operator notices. This is the same shape of
defect as `reports/wq4074_verify_wq4072_miko3_guard_bypass.md` (a safety claim that holds
for the tests shipped but not for the guarantee actually promised) and lands on exactly
the axis `docs/plugin-spec.md:370-399` (wq#4147, SDR TX) already flagged as an open
design gap for this project: an action whose risk is bounded by the device under test,
wrongly filed under "passive" instead of "energize."

Per archetype 8, this escalates to opus-5@high for judgment: whether `classify_access`
should be reclassified `tools_energize.classify_access = true` (gated, consistent with
`halt`), or whether the implementation should be changed to avoid the raw `halt` (e.g.
read without halting where the target architecture allows it, or auto-`resume` on every
exit path including exceptions) so the "ungated" declaration becomes true instead of
aspirational — and whether the `[mcp.tools_energize]` manifest contract needs an explicit
rule that a tool declared ungated may not itself invoke a verb the same manifest marks
gated.

ISSUE-LOG rows 1401 (fleet-search outage hit during this verify-pass).
