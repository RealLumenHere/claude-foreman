# wq#3289 — recycle stale swap, identify the top swapper

2026-10-01 audit wall-5: `free -h` showed Swap ~12Gi used / 23Gi, Mem available
~49Gi. `vm.swappiness` is already 10 (wq#15/17); the used swap is cold pages
from an earlier memory-pressure spike that never got paged back in. `swapoff
-a && swapon -a` forces them back into RAM and leaves swap empty, safe only
while `MemAvailable` comfortably exceeds `SwapUsed` — true here (49.7G vs
12.9G), so this is a "quiet minute" by the ticket's own definition.

## Top swapper (measured 2026-10-01, `swap_recycle.py top`)

```
1.77G   pid=2955    python3   -> /home/lumen/ab_rag/rerank/.venv/bin/python3 rerank_server.py
0.76G   pid=631709  litellm
0.30G   pid=2453186 beam.smp
0.29G   pid=531518  node      -> n8n
0.27G   pid=6735    celery
```

The ticket's hint ("lumen-re-sync peaks 3.7G mem / swaps") does not match any
currently running process (no `re-sync`/`resync` cmdline on the host at
measurement time) — it was likely a transient peak from the original 2026-10-01
audit snapshot, not a steady-state process. The verified top swapper right now
is `rerank_server.py` (ab_rag/rerank) at 1.77G `VmSwap`, well under the host's
49G available memory, so it is not itself the reason swap is non-empty — it is
one of many long-lived processes holding cold pages the kernel never bothered
to reclaim.

## Run

Read-only, no root:

```
python3 ops/wq3289-swap-recycle/swap_recycle.py top       # top VmSwap processes
python3 ops/wq3289-swap-recycle/swap_recycle.py check      # MemAvailable vs SwapUsed, safe=True/False
python3 ops/wq3289-swap-recycle/swap_recycle.py recycle    # dry run of the plan
```

`swapoff`/`swapon` need root. Per repo convention (see
`ops/wq2094-root-migration`), run privileged work detached, never as a
foreground sudo:

```
lumen-run start --root --name swap-recycle -- \
  python3 /home/lumen/foreman/ops/wq3289-swap-recycle/swap_recycle.py recycle --execute
```

`recycle` re-checks the `MemAvailable >= SwapUsed` precondition itself right
before acting and refuses (exit 1) if it no longer holds, so a late-arriving
memory hog between `check` and the detached run cannot push the host into OOM.

## Tests

`python3 -m unittest discover -s ops/wq3289-swap-recycle` — meminfo parsing,
the safe/unsafe/boundary precondition, and VmSwap scan ordering against a
synthetic `/proc` tree (no real host state touched).
