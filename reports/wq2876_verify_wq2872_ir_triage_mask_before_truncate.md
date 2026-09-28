# wq#2876 verify: wq#2872 (ir_triage masks before the display cut)

## Verdict
CONFIRM, with one caveat about the probe (below). The wq#2836-ir-triage bundle had no
`--verify` handler, which is what blocked the earlier worker attempts; this PR adds one.

## Checked
- `collect_aq` and `collect_trivy` call `mask()` on the raw field first and cut to the
  display budget after (ir_triage.py `collect_aq`, `frag`). `frag` masks again and cuts at 400
  after masking, so no raw credential is ever cut.
- Live `/home/lumen/minerva/ir_triage.py` is byte-identical to the bundle (sha256 e0cb2f28),
  both unit files match `/etc/systemd/system`, the timer is enabled and active.
- `ops/wq2836-ir-triage/test_ir_triage.py`: 17 passed.
- New `install.sh --verify` handler: exit 0 on the live box; with the live script moved
  aside it prints `MISS absent: /home/lumen/minerva/ir_triage.py` and exits 1, so it can fail.
- Adversarial probe: an aq message with a dummy github-shaped token placed so the old 200-char
  cut lands inside it, 37 offsets from 150 to 258. Zero characters of the body reach the
  output on the current code.

## Caveat
The same probe against the pre-fix file (`5ac1f36b^`) also shows zero leaks. The orphan-prefix
backstop that landed in harness/masking.py afterwards (e2dbe416, 391a11b8) now masks a prefix
plus a short body run on its own, so on today's masker the reordering is defence in depth
and the probe can no longer tell fixed from unfixed. The wq#2872 numbers (24 and 10 clear
characters on pre-fix HEAD) were measured before the backstop. The ordering is still the
correct one and stays.

## Process note
`install.sh` had no `--verify` marker, so running it with that flag ran the install
steps. The script copy to `/home/lumen/minerva/ir_triage.py` executed (it already matched the
bundle apart from the lock guard in d0b44687); the systemd steps then failed on
permissions before any daemon-reload, enable or start. Units and timer are unchanged.
The new handler closes that trap for the next caller.
