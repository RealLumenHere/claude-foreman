# Squidbrake observe-mode pilot (wq#4750, wq#4744 T6)

Squidbrake is the **human review-hold** layer for interactive RE sessions: it holds a
hardware call until a person approves it. Nothing else in the fleet does this
(`../gap-analysis.md` §2 table, "Squidbrake"). This directory holds the hardware ruleset,
the pilot plan, and the tool that scores the observe-mode log.

## Scope (who gets it)

In scope — interactive sessions **with a human approver present**:

- sessions whose cwd is under `/home/lumen/re`, and/or
- sessions with a hardware MCP server registered (`openocd`, `limpet-flashrom`,
  `serial-maksoor`; `claude-mcp-add.sh` registers these for `/home/lumen/re` only).

**Out of scope — headless fworker sessions never get Squidbrake.** No human is there to
approve, so a hold would deadlock. For them a hardware RED stays a hard deny in
action_gate (`../gap-analysis.md` §3; judge floor T4/wq#4748). `rules.yaml`
`scope.exclude_headless_fworker` and `observe_report.py` both enforce this.

Squidbrake sits **above** guard.sh/action_gate and never replaces them. Every layer still
runs: action_gate still gates (and for headless still hard-denies) the same commands;
Squidbrake only adds the review-hold for interactive operators.

## Layer order (no double gating)

1. Squidbrake (this, interactive only) — human approval hold
2. guard.sh + action_gate (`harness/action_gate.py`) — unchanged
3. worker-settings.json allowlist — unchanged, deny by default

## Files

| file | what |
|---|---|
| `rules.yaml` | hardware ruleset: `hold`/`allow` per MCP tool and per CLI command class. Source of truth for the report. |
| `observe_report.py` | replays an observe-mode event log through `rules.yaml` and reports held / would-have-held / false-positive rates + a per-rule breakdown. `--help` for usage. |
| `events.sample.jsonl` | committed sample log; documents the JSON-Lines event format. |
| `REPORT.sample.md` | report rendered from the sample log (what the weekly report will look like). |
| `tests/` | pins the classification and rates. |

## Rule classes

`rules.yaml` holds the call for: flash/erase (MCU/SoC flashers + MTD), eFuse/OTP burns,
voltage/clock changes, openocd JTAG/SWD energize + target memory/flash write, flashrom
write/erase, and serial `open`/`send`/DTR/RTS/BREAK. Read-only and passive surfaces
(memory reads, flash reads, `list_ports`, sigrok logic capture) pass straight through. An
**unlisted** tool on an energizing MCP server defaults to hold (fail safe); an unlisted
tool on the passive sigrok server defaults to allow. The CLI patterns mirror
`harness/action_gate.py` `BUILTIN_FLASH`/`BUILTIN_RED` so the two layers agree.

## Pilot procedure

1. **Schema reconciliation.** Squidbrake is young upstream (versions drift). Before
   install, diff `rules.yaml`'s field names against the installed squidbrake rule schema
   and adjust the *wrapper* fields (`mode`, `when_unreachable`, `scope`) if upstream
   renamed them. The `rules[*].match_mcp` / `match_cmd` / `action` fields are what
   `observe_report.py` reads and must not be renamed without updating the tool.
2. **Install in observe mode.** `mode: observe` — Squidbrake holds nothing; it logs what
   it *would* have held. `when_unreachable: block_risky`. Register only in `/home/lumen/re`.
   Do **not** run `squidbrake lockdown` during the pilot (it writes managed settings that
   disable `--dangerously-skip-permissions`; that is a separate hardening step, T5/wq#4749).
3. **Run one week.** Operators work hardware RE in `/home/lumen/re` as normal. As they go,
   annotate each would-have-held event in the log with `operator.verdict`:
   `legit` (a real energize they approved), `denied` (a real catch they would have
   blocked), or `benign` (a false positive — should not have been held).
4. **Report.**
   ```
   python3 observe_report.py --rules rules.yaml --log <week.jsonl> --out REPORT.md
   ```
   Reports would-have-held count/rate, false-positive count/rate, true-catch rate,
   annotation coverage, and holds-by-rule.

## Decision gate — observe → review

Flip `rules.yaml` to `mode: review` (Squidbrake starts actually holding) only when the
week's `REPORT.md` shows **all** of:

- **false_positive_rate < 0.10** — fewer than 1 in 10 holds were benign. A higher rate
  means operators will learn to approve reflexively; fix the misfiring rule first.
- **annotation_coverage ≥ 0.80** — enough of the holds were triaged for the rate to mean
  something. (The pilot can only fail honestly if operators actually annotate.)
- **at least one `denied` verdict**, or an explicit operator sign-off that the holds that
  fired were the right surfaces. A pilot where nothing risky ever happened has not shown
  the hold earns its interruption cost.

If false_positive_rate ≥ 0.10, tighten the offending rule(s) (see holds-by-rule) and
re-run observe for another week rather than shipping review with a noisy ruleset.

## Status / follow-up

The ruleset, the pilot harness, the report tool, and this decision gate are authored and
tested. The **one-week observe run and the review-mode decision are an interactive
`/home/lumen/re` operator task** — a headless worker is out of scope by design (it cannot
be the human approver, and it does not get Squidbrake). That run + decision is the
remaining step; it is time-bound (a calendar week of real RE work), not code.
