# minerva-ref-sync — sweep-gated code mirror of /home/lumen/minerva

Why: workers may not read `/home/lumen/minerva` (credentials mirrored into `context/sections`,
wq#1467), but several tickets need its root-level code (`lumen_recall2.py`, `minerva_verify.py`,
`minerva_factpack.py`, design docs). `/home/lumen/research/minerva-ref` is a granted read root
(`harness/fleet-exec-policy.yaml`) holding a copy of that code WITHOUT the credential surface.
Decision record: `research/MINERVA_EVAL_GRANT_DECISION.md` §5.

Two layers, both required (Frank's condition, 2026-09-13):

- **Filter** — rsync exclusions in `minerva_ref_sync.py::EXCLUDES`: `context/`, `tests/`,
  `archival/`, `archive/`, `backups/`, `logs/`, `staging/`, `retention/`, `gate-redteam/`, `.git/`,
  `*.db*`, `*.bak*`, `*.pyc`, `*.hwm`, and every capability-lint deny basename.
- **Guarantee** — after the staging copy is built, `harness/read_dir_sweep.py` runs on the
  result. `hits == 0` publishes by atomic rename; anything else aborts, leaves the previous good
  mirror untouched, exits 2 and raises `aq` (`minerva-ref-sync`/`gate`) naming the file. A sweep
  tool failure exits 3 and also never publishes. The mirror cannot be published with a secret in
  it, including one in a file the filter never anticipated.

Cadence: hourly (`lumen-minerva-ref-sync.timer`, ±5 min jitter). Minerva changes constantly and
the unblocked tickets patch against a copy of the live working tree; an hour is close enough that
a worker's diff applies, and the daily runtime sweep (`lumen-read-dir-sweep.timer`) still gates
the granted root independently. Sync takes ~3 s (2.3k files, 16 MB).

Operate:

```
/home/lumen/bin/minerva-ref-sync --json          # sync now
/home/lumen/bin/minerva-ref-sync --dry-run       # build + sweep, never publish
journalctl -u lumen-minerva-ref-sync -n 20       # last runs
cat /home/lumen/research/minerva-ref/.minerva-ref-sync.json   # what is published
```

A refusal means someone put a secret into the live minerva tree. Fix the source, not the sync.
Never edit files in the mirror; the next run overwrites them.
