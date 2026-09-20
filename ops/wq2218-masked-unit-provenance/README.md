# wq#2218 — masked units get a provenance record

wq#2073: `lumen-pokemon-live` was masked 2026-09-13 with the reason recorded
nowhere; wq#1990 unmasked it in good faith and it ran 19h / 8478 brain calls.
`known-removed.txt` (wq#1041) covers removed units only, so a mask had no
equivalent.

Chosen fix: `retired_services.yaml` is the provenance record for masks too, and
`unit-drift check` points at it. A unit that is a `/dev/null` symlink and has no
entry there gets an `advisory:` line in the report (CLEAN or DRIFT). Advisory
only: exit code and `--digest` are unchanged. Fail-open if the yaml is
unreadable.

- `staged/unit-drift` — live `/home/lumen/bin/unit-drift` plus the change.
  `/home/lumen/bin` is a primary tree workers cannot write, so applying is one
  copy from a lane with that access:
  `cp ops/wq2218-masked-unit-provenance/staged/unit-drift /home/lumen/bin/unit-drift`
  (live file as of this ticket differs from the wq#1657 staged copy; this one is
  based on live).
- `test_masked_provenance.py` — `python3 ops/wq2218-masked-unit-provenance/test_masked_provenance.py`
- Entry added: `lpp (lumen-pokemon-live)` in `docs/fleet/retired_services.yaml`.
  `lumen-guardian-lpp.service` and `lpp-restart.service` are also masked and are
  not covered; the advisory names them until someone records why.
