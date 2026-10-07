# wq#3590 — verify-pass on wq#3522's ser2net bench-UART boot-log bundle

Verifier: claude-sonnet-5@high, fresh instance (same model as author, claude-sonnet-5).
PR under review: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1671 (merged,
`ops/wq3522-ser2net-boot-log-maksoor/{README.md,install.sh,ser2net.yaml.tmpl,test_loopback.sh}`).

## Verdict

**CONFIRM.** No contradiction found after a genuine attempt to break it — the one real
candidate bug traced out to a non-bug once followed all the way through gensio's actual
parser. Two non-blocking notes below, neither rising to CONTRADICT.

## What I tried to break

### 1. Multi-line YAML `connector:` fold — looked like a real bug, wasn't

`ser2net.yaml.tmpl`'s connection block writes the connector as a folded plain scalar:

```yaml
  connector: serialdev,
             /dev/ttyUSB0,
             115200n81,local
```

Parsed with PyYAML (`yaml.safe_load_all`, matching libyaml's folding semantics, which
`ser2net` itself uses — confirmed `#include <yaml.h>` / `yaml_parser_initialize` in its
`yamlconf.c`), this folds to a scalar with a literal space after each comma:

```
connector repr: 'serialdev, /dev/ttyUSB0, 115200n81,local'
```

I reproduced `test_loopback.sh`'s own `sed` substitutions against the real template
(`/tmp/fworker-wq3590/rendered_test.yaml`) and got the identical shape:
`'serialdev, /tmp/.../fakework/dut, 115200n81,local'` — so the shipped loopback test feeds
`ser2net` the same folded string production would. That looked like it should break device
open (leading space in the path) and/or the speed parse.

Pulled gensio's and ser2net's actual upstream source (`cminyard/gensio` @
`bc7bfdbc6f513ca44396cba73192677d7d29c23f`, `cminyard/ser2net` @
`699d63466f576e498161fcfaec88092fed7e4120`, via `curl` on raw.githubusercontent.com — github
code search found the files, `fleet-search github --code`) and traced the whole path:

- `yamlconf.c:process_scalar()` only expands `*(alias)`/`*{file}` — no whitespace
  trimming, so the folded space survives into `y->connector` verbatim. (Confirms the
  space is real, not a PyYAML artifact.)
- `gensio.c:str_to_gensio()` (`lib/gensio.c:1623`), the dispatcher `connector:` strings
  actually go through: after matching the `serialdev` type prefix it calls
  `gensio_scan_args()` (which just consumes the one comma, no `(` present), and **then
  explicitly does `while (isspace(*str)) str++;` before calling the handler** (`gensio.c`
  :1650-1651). That strips exactly the fold-inserted space before the device path ever
  reaches `iodev_gensio_alloc()`.
- The device/speed split inside `iodev_gensio_alloc()` (`sergensio_serialdev.c:1828`,
  `strchr(sdata->devname, ',')`) is a raw split with no trim, so the *second* fold-inserted
  space (before `115200n81`) does survive into `sdata->parms`. But that string is handed to
  `gensio_str_to_argv(..., sdata->parms, &argc, &argv, " \f\t\n\r\v,")`
  (`sergensio_serialdev.c:1600`) — a tokenizer whose separator set includes space
  *alongside* comma, so the leading space is consumed as a plain delimiter, not emitted as
  part of a token.

Net result: both fold-inserted spaces get absorbed by gensio's own parser before they can
corrupt the device path or the speed string. The multi-line form is not how the upstream
example writes it (upstream's own `ser2net.yaml` comment uses one line:
`connector: serialdev,/dev/ttyS0,9600n81,local`), so it's a stylistically risky choice, but
it is not broken. I was wrong on my first pass through just `sergensio_serialdev.c` alone —
only tracing back to `str_to_gensio()`'s dispatch loop (which I'd initially skipped past)
settled it. Logging the near-miss because it's exactly the kind of thing a shallower verify
pass would have escalated on a guess.

### 2. Option-name and syntax claims, checked against upstream, not trusted from the PR

- `kickolduser` — real, `cminyard/ser2net/portconfig.c`: `gensio_check_keybool(pos,
  "kickolduser", &port->kickolduser_mode)`.
- `trace-both` / `trace-both-hexdump` / `trace-timestamp` family — real,
  `portconfig.c` + documented in `ser2net.yaml.5`; upstream's own `ser2net.yaml` example
  uses `trace-both: '/var/log/trace-\p'`, same shape as the template's
  `trace-both: '/var/log/ser2net/dut1-\Y\m\D.log'`.
- `accepter: tcp,<addr>,<port>` address-bound form, and `socat`'s
  `PTY,link=PATH,raw,echo=0` — both match what's documented upstream.

### 3. install.sh / test_loopback.sh control flow

- Step order (apt install → disable packaged unit → resolve real tailnet IP → loopback
  test → render+install own config → enable) matches the README's and ticket's claims, in
  that order, in the file.
- `test_loopback.sh`'s cleanup trap, port-up polling, and the final byte-crossing check
  (`BOOT-LOG-OK` round trip over `/dev/tcp`) are straightforward and don't have an
  obvious false-pass path — a missing `socat`/`ser2net` binary fails loud ("command not
  found" → the up-loop times out → explicit `FAIL`), not a silent pass.

## Two non-blocking notes (not a CONTRADICT)

1. **`install.sh --verify`'s explicit `0.0.0.0` grep only runs in the local
   (`hostname == maksoor`) branch**, not the remote-ssh branch (`ops/wq3522-.../install.sh`
   :39 vs :40-49). This doesn't actually weaken the guarantee: both branches gate on
   `vfile`/`vremote_file`, a byte-identity check against a copy rendered locally from a
   verified non-empty `tailscale ip -4` result — which can never be `0.0.0.0` — so the
   remote branch's safety property holds even without the redundant grep. Still, the
   asymmetry reads as an oversight; trivial to add `grep` over the SSH'd-back content for
   parity.
2. **`test_loopback.sh` was never actually executed** — not by the original author, not by
   me. Neither this worktree nor (per the PR's own text) the author's had `ser2net`/`socat`
   installed, and `apt-get` is not on the BUILD-worker allowlist in either case
   (`worker-settings.json` has no `apt-get` entry). The PR's "Status: NO-PROBE, shipped
   undeployed" section scopes the gap honestly to "undeployed to maksoor" and doesn't claim
   an actual run, so this isn't an over-claim — but it does mean "loopback-tested" is, for
   now, a property of the code's design (now independently traced against upstream source
   above), not of an observed pass. Worth a real run the first time any Lumen BUILD worker
   has `ser2net`+`socat` available (or maksoor itself, once reachable) — until then this
   stays a design-level confirmation, not an execution-level one.

## Evidence trail

- `/tmp/fworker-wq3590/parse_check.py` — PyYAML repr-dump of the rendered template and of
  the exact `test_loopback.sh`-shaped render.
- `/tmp/fworker-wq3590/{gensio.c,utils.c,sergensio_serialdev.c,yamlconf.c}` — pulled via
  `curl` from the exact commits cited above, grepped/read for the dispatch and tokenizing
  logic cited.
- `fleet-search github --code` located the four upstream files and confirmed
  `kickolduser`/`trace-both` option names and the single-line example syntax.
