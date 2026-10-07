# wq#3558: Lumen ollama scheduler wedge (chat hangs, nomic-embed stays resident)

## Root cause

Lumen's ollama is 0.34.4 with `OLLAMA_MAX_LOADED_MODELS=1`, `NUM_PARALLEL=1` and
`KEEP_ALIVE=30s`. minerva's three embed clients (`minerva_memory.embed`,
`minerva_skills.embed`, `minerva_verify._embed_batch`) sent `keep_alive: '30m'` on
every `/api/embed`.

In upstream `server/sched.go` at v0.34.4:

1. A chat request for a cold model goes to `pendingReqCh`. `processPending` hits the
   max-runners branch, sets `sessionDuration = 0` on nomic (line 349), and sends no
   expire because an embed is in flight (`refCount > 0`, line 350). It then blocks on
   `unloadedCh` (line 360). That loop is single-threaded, so every later request for
   any non-resident model queues behind it.
2. A request for the resident model skips the queue: `getRunner` lines 206-207 call
   `useLoadedRunner`, which sets `runner.sessionDuration = pending.sessionDuration`
   (lines 488-490). That replaces the unload mark with 30m.
3. When the in-flight embeds finish, `processCompleted` sees `sessionDuration = 30m`.
   It arms a 30-minute timer instead of expiring the runner (lines 391-410). Every
   later minerva embed re-arms the timer, so the unload never lands while embeds keep
   coming.

That matches every symptom. `/api/embed` and `/api/ps` kept answering. Chat logged
nothing after 07:15:33Z, when an embed reloaded nomic. `expires_at` sat ~30 min out.
litellm connections stuck, and `systemctl restart ollama` cleared it. A request with
no `keep_alive` passes a nil duration and leaves the mark alone. litellm sends none,
so the chat side never triggered this.

Upstream: no `sched.go` change in v0.34.4...v0.35.1 (15 commits, none scheduler); the
nearest open issue is ollama#17428 (embedding runner stuck in Stopping). Upgrading is
not a fix.

## Evidence (live, Lumen, 2026-10-07 ~11:0xZ, `repro.py`)

E1 = 96-text embed (~16 s on CPU). C = lfm2 chat sent 0.5 s after E1. E2 = a 1-word
embed sent while E1 is in flight. Times are seconds from start.

| run | E2 keep_alive | E1 end | nomic after E1 | C done |
| --- | --- | --- | --- | --- |
| control (no E2) | — | 16.83 | unloaded | 19.00 |
| wedge | `3m` | 24.20 | still resident, C pending 40 s later | 65.89, only after manual `keep_alive:0` at 64.20 |
| client fix | none | 16.99 | unloaded | 18.44 |
| wedge + class 7 | `3m` | 16.95 | still resident, C pending 40 s later | 58.82, after `selfheal_sweep.py --only ollama` at 56.96 (`pinned 139s > 120s; break 1/3`) |

Re-run: `python3 repro.py control`, `python3 repro.py wedge --e2-keep 3m --observe 40
--unwedge 1`, `python3 repro.py wedge --e2-keep none --observe 40 --unwedge 1`. A wedge
run blocks Lumen's chat models for at most `--observe` s, plus up to `--e2-keep` if
the unwedge call fails.

## Fixes

- Client (root cause): minerva branch `fix/wq3558-embed-no-keepalive-pin` drops
  `keep_alive` from all three embed calls. `tests/test_embed_no_keepalive.py` fails
  3/3 on origin/main fb441d4 and passes 3/3 on the branch.
- Self-heal: fleet-selfheal class 7 (`ops/wq3354-fleet-selfheal/sh_ollama.py`) breaks
  any later pin within one sweep (1-minute timer) using ollama's own unload call, with
  no restart. Deploy: `sudo bash ops/wq3354-fleet-selfheal/install.sh`. The class runs
  on built-in defaults because the live `selfheal.yaml` is never overwritten.
