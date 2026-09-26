# wq#2158 — verify of wq#2140 (PR #708, research/ISIS_POWEROFF_RCA.md)

Verifier: claude-sonnet-5. Verdict: **CONTRADICT (partial)** — one falsification claim is not supported by the RCA's own retained data; the recommended actions (P1, P2, P4) stand.

## Verdict per claim

| RCA claim | Result | Evidence |
|---|---|---|
| 1. fan never ramped, 789-797 rpm incl. 84 % CPU pre-change | **CONFIRMED** | netdata 09-20 15:15-15:21Z: fan 789.2-790.0 while cpu 82-84 % and Te0T 59.8 -> 71.6 |
| §3 trace numbers (Te0T 59.9 -> 71.4, TC0P 27 -> 44.3, cpu 84) | **CONFIRMED** | same re-run (`ops/netdata_window.py`), values match within 1 min-bucket rounding |
| 4. no thermal runaway, boot -2 died at ~0.4 % CPU | **CONFIRMED** | netdata 02:00-02:47Z: cpu 0.3-0.4, Te0T 83.8 peak, TC0P <50 |
| 3. "boot -4 ended abruptly with both GPUs bound and disable-gpus never yet run, so the failure predates the change" | **CONTRADICTED** | see below |
| 5. unit is broken / costs power | plausible, consistent with (3) below; not independently re-measured (plugs.json outside worker dirs) |
| 2. 01:04:08 ending = spurious USB power key | not checkable (no isis journal grant) |

## Claim 3: a GPU thermal step happens in boot -4, 1 h 38 m before it died

RCA §1/§0 row 3 rests on "`disable-gpus` first activation 09-19 15:13:39", i.e. after boot -4 ended at 10:47:46.
The retained netdata series says something changed on isis at **~09:08-09:10Z on 09-19, inside boot -4**, at idle
(cpu 0.4 %), which is the same minute the RCA dates the unit file's creation (09:08:23):

```
python3 ops/wq2158_netdata_trace.py 1789806600 1789810200 30 'temp[0-9]+_(TM1a|TM2a|TC0P|Te0T|TG0D|TG1D)_input$|^system\.cpu$'
timeZ TC0P TG0D TG1D TM1a TM2a Te0T cpu
09:06 44.2 45.0 47.3 58.4 50.7 79.2 0.6
09:08 44.2 45.0 47.2 58.4 50.7 79.2 0.4
09:10 44.2 46.1 49.5 58.3 50.6 79.2 0.4   <- GPU diodes leave a flat line
09:14 44.8 48.1 52.0 58.4 50.9 79.3 0.4
09:20 45.8 49.5 53.5 59.0 51.5 79.6 0.4
09:30 46.8 50.5 54.6 59.6 52.2 80.2 0.4
```

TG0D / TG1D (the two GPU diodes) climb +5.5 / +7.4 C over 20 minutes with the CPU idle and never come back
(51.5 / 55.6 at 10:45; TC0P 44.2 -> 47.9). Boot -4 died at 10:47:46. An earlier, smaller step at 08:40-08:46 (TM1a 54.6 -> 57.8)
is unexplained but the GPU diodes stay flat through it (44-45 / 46-47), so it is not the same event. Nothing in the RCA explains a GPU-diode step at 09:09 with the cards bound and
dpm=low unchanged; a live application of the unit's ExecStart (PCI-remove without dpm) does, and matches RCA §5b ("cards leave the
bus with `dpm=low` no longer applied").

So the pre-change instance the RCA leans on is not established. The honest reading is: **all three abrupt endings (09-19 10:47,
09-20 02:47, 09-20 15:33) are within ~1.6 h - 30 h of the ~09:09 GPU change; the only unbroken boot (-5, 18 days) is before it.**
The RCA's "GPU hypothesis falsified" is at most "the specific fan-ramp mechanism is falsified"; the GPU change as cause is
**not excluded** and P3 ("the GPU change is not the cause") is unsupported.

What to check (needs an isis journal grant, which this worker lacks): `journalctl -b -4 -u disable-gpus`, and
`journalctl -b -4 --since 09:05 --until 09:15` for the PCI remove / amdgpu teardown. If the unit or a manual remove ran at
~09:09, claim 3 is wrong outright.

## Other findings

- **Omission in §4.** The ticket's suspect sensors were the DIMMs. Boot -1 peaked TM2a 73.8 C and TM1a 70.8 C (vs 47-52 / 54-60 idle
  pre-change); the RCA reports only TC0P 53 / Te0T 84. Not near DDR3 limits, but the ticket's own number should be addressed.
- **Boot -1 was not under load at the end.** netdata cpu is 0.3-0.4 % from 15:24 to 15:30 (load ended ~15:23), then cpu stops
  reporting; TC0P climbs 46.5 -> 52.6 over 15:30-15:34. "Under load at death" holds only if load resumed after 15:30.
- **`ops/smc_probe.py` fails silently.** With no grant, `fleet-exec run isis` prints `DENY [grant:no_ssh_run]`, `read()` gets zero lines,
  and `labels` prints `isis: 0 sensors` with exit 0 (ran here). It should exit non-zero on `out.returncode`.
- Ops: retiring the unit (P1/P4) is still correct: it violates the June standing rule regardless.

## Not verified

isis/hathor journals, plugs.json (outside allowed dirs), lumen_state. No load test run (not permitted).
