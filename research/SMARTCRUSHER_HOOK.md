# wq#888 — SmartCrusher hook point in the claude-code tool-result pipeline

ticket: wq#888 | date: 2026-08-24 PT | deps: wq#633 | ref: research/HEADROOM_EVAL.md §4

## VERDICT: **hook point exists, payload does not — build it, do not wire it**

A working hook point was found and a lossless prototype ships with this ticket
(`harness/crush.py`, 18 tests). It is not wired into `guard.sh`, because measuring
the surface it can actually reach across 39 real sessions gives **0.07% of
tool-result bytes**. HEADROOM_EVAL's "93.7% JSON" counted the tool-result
*envelope*, which no hook can touch; the share a hook *can* touch is **1.7%**.

The two options the ticket asked about are dead for structural reasons (§1), the
third is a payload problem rather than a plumbing one (§3), and the byte budget
that actually matters turns out to sit somewhere else entirely (§5).

## 1. Hook-point inventory

| Candidate | Verdict | Why |
|---|---|---|
| fworker post-processing of stream-json | **impossible** | stream is one-way and post-hoc |
| PostToolUse hook rewriting the result | **not supported** | no such field in CLI 2.1.233 |
| PreToolUse `updatedInput` + pipe filter | **works** | already the wq#729/wq#730 lever |
| MCP-served replacement for Bash | possible, not justified | §4 |
| headroom proxy mode | still BLOCKED | unchanged from HEADROOM_EVAL §4 |

### stream-json post-processing (ticket option 1) — impossible

`fworker.py:743` spawns the child with `stdin=subprocess.DEVNULL` and reads only
stdout. There is no return channel, and adding one would not help: a tool result
is appended to the child's message array *before* the corresponding stream-json
event is emitted. The stream is a read-only mirror of a context that is already
committed. Compressing it changes bytes in fworker's memory and zero bytes in any
request body.

Switching to bidirectional `--input-format stream-json` does not open a door
either. That mode carries user messages inbound; tool execution stays internal to
claude-code, and there is no inbound frame that edits a result already recorded.

### PostToolUse rewriting the result — not supported in 2.1.233

String-searched the shipped binary
(`@anthropic-ai/claude-code/bin/claude.exe`, v2.1.233, 324 MB, not stripped):

| String | Occurrences |
|---|---|
| `updatedInput` | 269 |
| `PostToolUse` | 194 |
| `additionalContext` | 186 |
| `updatedOutput`, `updatedToolResult`, `modifiedOutput`, `updatedResult`, `replaceOutput`, `rewriteOutput`, `toolResultOverride`, `updatedToolResponse` | **0 each** |

PostToolUse exists and its documented outputs are `decision`/`reason` and
`hookSpecificOutput.additionalContext`. Both *append* to context. Neither replaces
the result, so PostToolUse can only make a session more expensive.

That leaves exactly one lever that changes bytes before they enter context:
PreToolUse `updatedInput`, which rewrites the tool *call*. `guard.sh:246-249`
already uses it to append `| head -c 2000` to every Bash command, and
`guard.sh:331-339` to inject a default `limit` into every unbounded Read.

### The hook that does exist

Because the lever rewrites the command, the compressor has to be a stdin→stdout
filter spliced into the pipeline:

```
{ <cmd>
} 2>&1 | crush.py --max-bytes 2000
```

Ordering matters. This runs *before* the wq#729 cap rather than replacing it:
truncation throws the tail away, compression keeps every value and drops only
repeated bytes, so the same 2000-byte budget carries strictly more information.
`harness/tests/test_crush.py::test_cli_max_bytes_caps_after_compressing` asserts
that property directly.

## 2. What the hook can reach — measured

`research/smartcrusher_probe.py` over all 39 sessions in
`/home/lumen/agents/work-queue/_runs/` (the wq#704-corrected corpus, same source
as `call_audit.py`): 4,913 tool results, 8,547,971 bytes.

| Tool | Results | Bytes | Share |
|---|---:|---:|---:|
| Bash | 3,687 | 4,885,691 | 57.2% |
| Read | 616 | 3,500,114 | 40.9% |
| Agent / Edit / Write / rest | 610 | 162,166 | 1.9% |

Only Bash is pipeable, so 57.2% is the ceiling before asking anything about
content. Within it:

- **61 of 3,687 Bash results (1.7%) have stdout that parses as JSON or JSONL.**
  56 JSON, 5 JSONL.
- On that subset `crush.py` gets **20.4%** (29,864 B → 23,765 B).
- Net: **6,099 B saved = 0.12% of Bash result bytes, 0.07% of all tool-result
  bytes.**

### Reconciling with HEADROOM_EVAL's 93.7%

Both numbers are correct about different objects. HEADROOM_EVAL §2 classified the
tool-result *wrapper* — the `{stdout, stderr, exitCode}` envelope — and that
envelope is a JSON dict 93.7% of the time. But claude-code assembles the envelope
*after* the tool runs, downstream of every hook. A filter inside the pipeline sees
only the stdout bytes, and those are JSON 1.7% of the time.

This is the correction that changes §4's conclusion: SmartCrusher's 60-95% figure
is for JSON arrays, and the reachable part of our payload is essentially not JSON.
The engine was never the constraint.

### Why the achieved 20.4% is below the claimed 60-95%

The 61 addressable payloads are mostly single small objects (`{"busy": false}`,
one-record `wq show` output), not the long homogeneous arrays the columnar trick
pays off on. The five JSONL results are the shape SmartCrusher is built for; the
best single result compressed 3,706 B → 2,164 B (42%). There simply are not many
of them.

## 3. The prototype

`harness/crush.py` — deterministic, dependency-free, 227 lines. Minifies, then
rewrites homogeneous object arrays into `cols`/`rows` with constant columns
hoisted into `const`, and reads JSONL as an array. It holds four properties, each
tested:

- **Lossless.** `decode(encode(x)) == x`. The probe re-asserts it on every real
  payload it compresses, so the savings figure above cannot be inflated by
  silently dropping data.
- **Self-describing.** `{"__crush__":"cols+rows","cols":[…],"rows":[[…]]}` is
  meant to be read by the model with no decoder and no prompt-side explanation.
  An encoding the reader must be taught costs prompt tokens in every session,
  which is the cost being cut.
- **Never worse.** Output is compared against input at every stage and the smaller
  wins, so a payload that does not compress passes through byte-identical.
- **Bails rather than guesses.** Arrays are columnar-ized only when every element
  has an identical key set; a union-of-keys encoding would need an absence
  sentinel that could collide with real data. A payload already containing
  `__crush__` is returned untouched.

### Why not headroom itself

`pip install headroom-ai` is not reachable from a worker (package installs are not
allow-listed, confirmed this session), and the dependency would buy nothing here:
SmartCrusher is deterministic key-collapsing, which is ~120 lines. The reason to
take the dependency would be the ML engine, and HEADROOM_EVAL already ruled that
out on Broadwell. If proxy mode is ever unblocked, headroom becomes interesting
again for reasons that have nothing to do with this hook point.

### Why it is not wired into guard.sh

`guard.sh` runs on every Bash call in every worker. Adding a branch and a Python
process spawn there costs real latency on 3,687 calls to save 6,099 bytes. Wiring
it on would be a net loss today.

The prototype ships standing by rather than deleted because the payload mix, not
the mechanism, is what fails. A lane that reads JSONL heavily (events logs, `_runs`
transcripts, `wq` exports) flips the arithmetic, and turning it on is then a
two-line `guard.sh` change with the numbers already measured.

## 4. MCP-served tools — the one place we would own the bytes

For an MCP tool we host, the result bytes are ours before claude-code ever sees
them, so compression needs no hook at all. That is a real difference from the
built-ins, and `ops/wq829-mcp-gateway` means the plumbing exists.

It still does not pay. Replacing Bash with an MCP tool would surrender the
persistent shell, the sandbox, and the background-task machinery, add tool-schema
tokens to every session's prefix, and then apply compression to the same 1.7% of
stdout that is JSON. The ticket's option 3 (explicit `headroom_compress` calls) is
worse again: HEADROOM_EVAL §3 already noted the agent has to learn when to call
it, and a worker that spends a turn deciding to compress has spent more than it
saved.

## 5. Where the bytes actually are

The probe found the real target while measuring the wrong one.

**688 Bash results exceed the wq#729 2000-byte cap, totalling 3,403,898 bytes** —
70% of all Bash result bytes, in a fleet that is supposed to cap Bash at 2000. The
commands behind them are `grep` (102), `cd` (77), `sed` (67), `[` (59), `cat` (56),
`echo` (33), `ls` (28), `export` (27), `git` (27), `tail` (26). The largest single
result is 29,649 B. The `cd`/`export` heads — 104 of the 688 — are `guard.sh`'s
`SKIP_CAP` class, deliberately exempted so a shell state change is never lost
inside a subshell. The rest predate the cap or reached the model another way.

Closing that gap is worth ~500× what this ticket's compression is, and it is a
correctness fix rather than a new mechanism.

Two further leads, both cheaper than any compression work:

- **`BASH_MAX_OUTPUT_LENGTH` appears in the 2.1.233 binary** (9 occurrences;
  `MAX_MCP_OUTPUT_TOKENS` 6, `CLAUDE_CODE_MAX_OUTPUT_TOKENS` 10). If claude-code
  honours it natively, `guard.sh` can drop the `head -c` command rewrite entirely
  and the `SKIP_CAP` hole closes for free, because a native cap truncates the
  result instead of the pipeline and cannot swallow a `cd`. Needs one live session
  to confirm; not tested here, since this ticket spent no quota.
- **Read is 40.9% of result bytes and no pipe can reach it.** Only wq#730's
  `offset`/`limit` bounding applies. If tool-result cost is the target, Read is
  the second-largest line item and is entirely a guard-side problem.

## 6. Recommendation

- **Now:** none of the three options in the ticket. Merge the prototype and the
  probe as the record; leave the hook unwired.
- **Next:** wq#951, seeded from this ticket — the 688 uncapped Bash results and
  the `BASH_MAX_OUTPUT_LENGTH` test. That is the 3.4 MB.
- **Later:** re-run `smartcrusher_probe.py` if a JSONL-heavy lane appears. The
  wiring recipe is in §1 and the filter is tested.
- **Unchanged:** proxy mode stays BLOCKED on the OAuth test (HEADROOM_EVAL §4).
  Nothing found here bears on it either way.

## Reproduce

```
python3 research/smartcrusher_probe.py          # surface + achieved savings
python3 -m pytest harness/tests/test_crush.py   # 18 tests, zero quota

# a payload the filter is actually for: 6 records, indented, one constant column
python3 -c 'import json;print(json.dumps([{"id":i,"lane":"foreman-tok","state":"open"} for i in range(6)],indent=2))' \
  | python3 harness/crush.py --stats
# -> {"in": 416, "out": 118, "kind": "json", "saved_pct": 71.6}
```

A two-record payload passes through unchanged: `cols`/`rows`/`const` scaffolding
costs more than two repetitions of two short keys, and "never worse" then keeps
the original. That is the expected behaviour, not a failure — and it is most of
what the 1.7% addressable set looks like.

Binary string counts:
`grep -ac "<string>" ~/.npm-global/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe`
