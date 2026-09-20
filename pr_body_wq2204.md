## What
wq#2204: the wq#1613 state verifier's `endpoint_live` and `node_storage` probes returned values they never observed, so `reconcile` superseded stored facts with them (same class as wq#2192).

## Why
- `probe_endpoint_live` returned the HTTP status ("200") as the observation for `url` / `port` facts; the registry maps both predicates to it.
- `probe_node_storage` returned only the first DIMM size for `ram` (stored value is a total) and an `lsblk` byte sum for `storage` (stored value is free text). `lsblk -J` without `-b` also yields string sizes, so the old `int()` sum errored on every storage fact.

## How
- `endpoint_live`: a good GET (2xx/3xx) observes the fact value; a bad status, transport error, or a value that is not an http(s) URL / numeric port is unknown.
- `node_storage`: sums every DIMM and every whole disk (`lsblk -b -d`, `type == disk`) and only confirms a stored value whose stated size matches within 5%; otherwise unknown. `storage_layout` is unobservable.
- `repair_clobbered_facts.py` also closes open `verify:endpoint_live` (url/port) and `verify:node_storage` (ram/storage/storage_layout) rows and reopens the predecessor each one superseded. Idempotent; `install.sh` already runs it.

## Testing
- `pytest ops/wq1613-minerva-verifier-registry`: 42 passed (14 new, incl. a sweep test that leaves url/port/ram/storage untouched and a repair test for the four clobber shapes).

## Risk / rollback
Deployed copy (`/home/lumen/bin/lumen-verify`) runs the old probes until `install.sh` is run post-merge (wq#2203); `install.sh` re-runs the repair. Rollback: revert the PR; reopened predecessor facts keep their original values.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
