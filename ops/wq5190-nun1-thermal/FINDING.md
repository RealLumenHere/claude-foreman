# wq#5190: nun1 (BC-250) thermal recheck, result and finding

Checked 2026-11-22 ~01:30 PST. Ticket: qwen3:14b at the soak duty cycle (60 s sleep) must stay
under 70 C Tctl. If it does not, file a BC-250 cooling finding before the buy-2-more decision.

## 1. The recheck never produced a result

`/home/lumen/scratch/bc250-soak/validate_qwen3_14b.log`:

| line | content |
|---|---|
| 3 | 2026-11-20 06:50:17 PST baseline `Tctl 85.8` (judge_bench wq#5125 already running) |
| 4-17 | 7 rounds, Tctl 83.1 to 86.8, edge 76 to 79 |
| 18 | `RESULT ... max_temp=86.8C (limit 70.0) VERDICT FAIL` (contaminated, wq#5126) |
| 19 | 2026-11-20 15:04:32 PST `THERMAL RECHECK GAVE UP: nun1 never idle 10 min within 8h` |

The log has no `THERMAL RECHECK: nun1 idle` marker after that, so there is no clean RESULT line.
`journalctl --user -u wq5126-thermal-recheck` shows the unit ran 15:04:21Z to 23:04:32Z on
11-20 and exited with status 3.

It cannot be re-run now. nun1 has been dark on tailnet and LAN since 2026-11-21 13:46Z.
Re-probed 2026-11-22 09:3xZ: `100.122.44.81:22` connect timed out, and `192.168.8.150:22`
returned "No route to host". The power cycle is a physical task, wq#5225 (frank_gated).

## 2. Why the waiter never fired (old run_thermal_recheck.sh)

- It counted nun1 as busy whenever Ollama `/api/ps` listed any model. That list shows models
  that are *loaded*, which is not the same as models that are *running*. If keep_alive is long,
  the list is never empty, so an idle blade reads busy forever. lumen_state `nun1.status` says
  "7 models loaded", which is consistent with that. nun1's keep_alive setting could not be
  checked because the node is dark.
- It was not judge traffic. `minerva.db judge_verdicts` has zero rows for any model between
  2026-11-20 13:00Z and 2026-11-21 00:00Z. The last nun1 judge call was 11-19 17:02Z.
- It recorded nothing about each sample, so the 8 h give-up cannot be diagnosed after the fact.
- Latent bug: when ssh or curl failed, the output was empty and the script counted that as
  IDLE. An unreachable nun1 could therefore have passed the idle wait.

Fix: `thermal_recheck.py` (this dir).
- Its primary idle signal is GPU load (amdgpu `gpu_busy_percent` <= 10 %). It falls back to the
  loaded-model list only when that counter is unreadable.
- "Unreachable" is its own state, and it resets the idle streak.
- It writes one trace line per minute to `<log>.trace`.
- The give-up line includes a count of samples per busy reason.

Tests: `test_thermal_recheck.py`, 10 passing.

## 3. Thermal data we actually have

- Every measured Tctl sample is between 83.1 and 86.8 C (log lines 3-18). All of them were
  taken while judge_bench was running at the same time, so they show continuous-load behavior.
- `soak.log` and `summary.log` contain **no temperature samples** (`grep -ci 'tctl\|temp'` = 0
  in both). The "60.8C Tctl at soak duty cycle" clause in `nun1.soak_result` has no source:
  no fleet log, event or episode contains a 60.8 C reading. The only "60.8" in the soak data
  is a round duration (`summary.log:602 ... deepseek-r1:14b ... 60.8s`). The clause was
  corrected on 2026-11-22.
- No sign of throttling: tokens/s for each model were the same in the first and last 25 % of
  the 48 h soak (nun1.soak_result). In the validate window qwen3:14b ran at 19.3-19.9 t/s,
  which matches the soak's 19.4 t/s.
- Reference point (lumen_state `bc250.cooling_and_fan_control`, from the elektricM BC-250
  cooling doc): CPU TJmax is 100 C. Stock heatsink with sustained 40CU at 2 GHz reaches TJmax
  and throttles. Capping the GPU governor at 1500 MHz holds about 83 C indefinitely. BIOS fan
  mode "Default" (40 % minimum) is described as inadequate.
- Prior art (fleet-search 2026-11-22: akandr/bc250, r/BC250Gaming multi-board LLM rigs): the
  community LLM builds also cap the governor at 1500 MHz and treat case airflow as the main
  constraint.

## 4. Decision: is 86-87 C Tctl under continuous load acceptable?

**Yes for one blade doing judge and general inference today, with conditions. No as the basis
for buying 2 more until the conditions below are met.**

- Acceptable: there is a 13 C margin to TJmax (100 C). 48 h at the soak duty cycle showed no
  throughput loss, and the 13-minute continuous-load window showed no throttling. The reading
  is within about 4 C of the community's "sustainable indefinitely" figure (about 83 C at
  1500 MHz).
- Not proven: we have no clean duty-cycle temperature at all, so the 70 C gate is still
  unmeasured. nun1 also went dark on 11-21 13:46Z with no recorded cause, and wq#5225 lists
  thermal as a candidate. Three blades sharing airflow will run hotter than one.

## 5. Thermal finding (filed as a follow-up ticket; it gates buy-2-more)

Once nun1 is back up (after wq#5225):

1. Read wq#5225's previous-boot journal. Check for thermal trips, MCEs and amdgpu hangs.
2. Record the governor cap actually in use (cyan-skillfish governor max frequency) and the BIOS
   fan mode.
3. Run `thermal_recheck.py` detached. It gives a duty-cycle RESULT and a trace.
4. Buy-2-more is acceptable only if both of these hold:
   - the duty-cycle max Tctl is under 70 C, or under 85 C with the governor capped at 1500 MHz
     and no throughput drop;
   - wq#5225 is not a thermal trip.

   Otherwise, fix cooling first (governor cap, fan mode, 4U12G airflow).
