"""wq#2358: triage a `[rotation] burned credential` ticket from the run's frozen
artefacts, as a command instead of an xhigh worker's hour.

Rows 577, 580 and 581 of the ISSUE-LOG each hand-rolled the same forensic replay and
reached "no credential burned"; ~32 rotation tickets were still open behind them. This
is that method with the judgement made mechanical. mask_replay (wq#2307) already owns
the corpus (snapshots, run records, the read cache re-cut to the bytes each Read saw,
the CLI transcript) and the hash-only record; this module adds the three things a
ticket triage needs on top:

1. The rule that FIRED. The alarm was filed under whatever harness/masking.py was
   committed at the ticket's `created_at`, and anchoring landed mid-cascade, so
   scoring under today's table alone reports "no hit" for a hit that was real then.
   The historic table is loaded from `git show <sha>:harness/masking.py` and scored
   alongside HEAD, instead of the frozen private copy mask_replay carries.
2. The live decision. Each match goes through burn_filter.classify() — the same
   function the tripwire pages on — so a triage verdict and an alarm cannot disagree
   about what counts as accounted. classify() greps the tracked tree with the value
   in a subprocess argv, the shape instinct 4 forbids for a real credential, so it is
   handed an in-process runner over the tree mask_replay already holds: nothing that
   might be a token is ever exec'd.
3. A control. Every run first plants a random ghp_ + 36 that exists in no committed
   file and requires it to come back UNACCOUNTED and exit 1 under the same rules and
   the same classifier. A triage that cannot report a real burn prints "clean" for
   everything, which is worse than no tool; if the control is missed, the run exits 3
   and prints no verdict at all.

Output is label, kept prefix, value length and 12 hex of sha256 per value — never a
value, and never the surrounding line either.

    python3 harness/mask_triage.py 2288                 # rotation ticket id
    python3 harness/mask_triage.py 2288 --source 2284   # name the run explicitly
    python3 harness/mask_triage.py 2288 2289 2290       # a batch: one line per ticket

A value HEAD's tree cannot explain is asked of the repo's history before it is called a
burn (wq#2359): `accounted: history:<blob>` means an earlier revision of a tracked file
held that exact string, which is what a run reading a worktree behind main was seeing.
The history index is built only when a first pass leaves something unaccounted, and the
control is re-run over it, since a wider tree is a wider excuse.

Verdicts: ACCOUNTED (the exact string is committed text), RULE-FIXED (unexplained by
the tree, but HEAD's table no longer reads it as a credential), RE-CUT (scored off a
file that changed since the read and no revision held those bytes: evidence of
nothing), UNACCOUNTED (nothing explains it and HEAD still matches: rotate), LIVE (it
is a credential in this process's environment: rotate).

Exit 0: nothing to rotate. 1: an UNACCOUNTED or LIVE value. 2: no corpus, or the
ticket cannot be resolved/dated. 3: the control failed; the tool cannot be trusted.
"""
import argparse
import json
import os
import re
import secrets
import sqlite3
import string
import subprocess
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import burn_filter  # noqa: E402
import mask_replay  # noqa: E402
import masking  # noqa: E402

WQ_DB = os.environ.get("WQ_DB", "/home/lumen/agents/work-queue/queue.db")
MASKING_REL = "harness/masking.py"
SOURCE_RE = re.compile(r"burned credential in wq#(\d+)")
# wq#2341 keys a rotation title by the burned VALUE, so rows filed since then name no
# run at all — one ticket now stands for every run that printed the same credential.
# The first of those runs is still in the DETAIL, where both the template and fworker's
# frozen fallback have always written it as `fired on wq#N`; the later ones arrive as
# notes. A title that does name a run still wins: the backlog is full of them.
DETAIL_SOURCE_RE = re.compile(r"fired on wq#(\d+)")
TREE = "<tracked>"  # the one "repo root" classify() is given; the runner ignores it

FATAL = ("UNACCOUNTED", "LIVE")
HISTORY_MAX_BLOB = 400_000  # a blob bigger than this is a binary or a vendored dump
EXIT_CLEAN, EXIT_BURN, EXIT_UNRESOLVED, EXIT_CONTROL = 0, 1, 2, 3


def ticket_row(ticket_id, db=WQ_DB):
    """(title, created_at, detail) of a queue ticket, or None. Read-only: never edits."""
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            return conn.execute(
                "SELECT title, created_at, detail FROM tasks WHERE id=?",
                (ticket_id,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def rules_at(created_at, repo=mask_replay.REPO_ROOT, sha=None):
    """(sha, patterns, skip_masked) of harness/masking.py as committed at `created_at`.

    The latest commit touching the file at or before the ticket was filed is the table
    that was live. `skip_masked` mirrors masking._matches (wq#2298), which only exists
    in tables that already ignore their own `<prefix>MASKED` output. None if the file
    had no revision yet, or the revision does not load.
    """
    sha = sha or mask_replay._git(repo, "log", "--before", created_at, "-1",
                                  "--format=%H", "--", MASKING_REL).strip()
    source = mask_replay._git(repo, "show", f"{sha}:{MASKING_REL}") if sha else ""
    if not source:
        return None
    mod = types.ModuleType(f"masking_{sha[:8]}")
    mod.__file__ = os.path.join(repo, MASKING_REL)
    try:
        exec(compile(source, f"{sha[:8]}:{MASKING_REL}", "exec"), mod.__dict__)
        patterns = [(label, rx) for label, rx, *_ in mod.PATTERNS]
    except Exception:  # a historic module is evidence, not a dependency
        return None
    return sha, patterns, hasattr(mod, "_matches")


def make_judge(tracked, live):
    """The `verdict` hook for mask_replay.replay: classify() with an in-process grep."""
    def runner(argv, **_):
        value = argv[argv.index("-e") + 1]
        found = mask_replay.account(value, tracked)
        return types.SimpleNamespace(returncode=0 if found else 1)

    cache = {}

    def judge(label, prefix, value):
        full = prefix + value
        if any(secret in full for secret in live):
            return {"live": True, "classified": "burned"}
        burned, _ = burn_filter.classify({label: [full]}, live, [TREE],
                                         runner=runner, cache=cache)
        return {"live": False, "classified": "burned" if burned else "benign"}
    return judge


def verdict_of(row):
    if not row["faithful"]:
        return "RE-CUT"
    if row["live"]:
        return "LIVE"
    if row["classified"] == "benign":
        return "ACCOUNTED"
    if "head" not in row["rules"]:
        return "RULE-FIXED"
    return "UNACCOUNTED"


def triage(source, rule_sets, tracked, items, live=()):
    """One row per distinct (label, value): which rule sets read it, and the verdict.

    `rule_sets` is [(name, patterns, skip_masked)]. A value is one row however many
    tables or artefacts carry it, so the count is of distinct strings.
    """
    judge = make_judge(tracked, set(live))
    rows = {}
    for name, patterns, skip_masked in rule_sets:
        for rec in mask_replay.replay(source, patterns=patterns, tracked=tracked,
                                      items=items, verdict=judge,
                                      skip_masked=skip_masked):
            rec.pop("line")
            row = rows.setdefault((rec["label"], rec["value_sha256_12"]),
                                  {**rec, "rules": []})
            row["rules"].append(name)
    for row in rows.values():
        row["verdict"] = verdict_of(row)
    return list(rows.values())


def exit_code(rows):
    return EXIT_BURN if any(r["verdict"] in FATAL for r in rows) else EXIT_CLEAN


def control(rule_sets, tracked, live=()):
    """True iff a planted, never-committed ghp_ + 36 is UNACCOUNTED and exits 1.

    Random per run, in memory only, so no file in any tree can hold it and nothing
    prints it; only its hash could ever appear, and it does not. Every rule set must
    read it (the historic table has to see a real token too), which is why the row's
    `rules` is compared to the whole list rather than each set run alone: alone, a
    value HEAD does not read is RULE-FIXED by definition.
    """
    alphabet = string.ascii_letters + string.digits
    planted = "ghp" + "_" + "".join(secrets.choice(alphabet) for _ in range(36))
    items = {"control:planted": f"remote: https://{planted}@example.invalid/o/r.git\n"}
    rows = triage(0, rule_sets, tracked, items, live)
    return (len(rows) == 1 and rows[0]["verdict"] == "UNACCOUNTED"
            and rows[0]["rules"] == [name for name, _, _ in rule_sets]
            and exit_code(rows) == EXIT_BURN)


def report(source, rows, out=None):
    out = out or sys.stdout
    fatal = [r for r in rows if r["verdict"] in FATAL]
    print(f"wq#{source}: {len(rows)} credential-shaped values in the run's model "
          f"context, {len(fatal)} to rotate", file=out)
    for row in rows:
        print(f"  [{row['verdict']}] {row['label']} len={row['value_len']} "
              f"sha256={row['value_sha256_12']} prefix={row['prefix']!r} "
              f"rules={','.join(row['rules'])}", file=out)
        print(f"      seen in : {row['first_seen_in']}", file=out)
        print(f"      accounted: {row['accounted_in']}", file=out)
    if fatal:
        print("\nROTATE: the UNACCOUNTED/LIVE values above are in no committed file "
              "(or are live). Hash a candidate against sha256 to name one.", file=out)
    else:
        print("\nNo rotation: every value is committed text, a string HEAD's table no "
              "longer reads as a credential, or an unrecoverable re-cut.", file=out)


def resolve(ticket, source=None, created_at=None):
    """(source run, alarm time) for a rotation ticket: the overrides, else its row."""
    row = ticket_row(ticket)
    named = (SOURCE_RE.search(row[0] or "")
             or DETAIL_SOURCE_RE.search(row[2] or "")) if row else None
    return (source or (int(named.group(1)) if named else None),
            created_at or (row[1] if row else None))


def history_text(repo=mask_replay.REPO_ROOT, max_blob=HISTORY_MAX_BLOB):
    """{history:<sha10> -> text} over every blob a ref of `repo` can reach.

    Reachable, not every object in the store: a blob only a dropped commit or a bare
    `git add` ever held is not something a reviewer can open, and "committed text" has
    to mean text someone can go and read.

    `tracked_text` accounts a value against HEAD's tree. A run that read a worktree
    behind main, or a fixture on a branch that never landed, read committed text all
    the same — and every reviewer of that revision could read it too, which is the
    whole test for whether a string is a secret. wq#2359: 5 of the 16 values the
    47-ticket rotation backlog could not account for are in this index, and clearing
    them settled 9 of the 13 tickets that HEAD's tree alone left open.
    """
    listing = subprocess.run(["git", "-C", repo, "rev-list", "--all", "--objects"],
                             capture_output=True, text=True).stdout
    shas, seen = [], set()
    for line in listing.splitlines():
        sha = line.split(" ", 1)[0]
        if len(sha) == 40 and sha not in seen:
            seen.add(sha)
            shas.append(sha)
    if not shas:
        return {}
    out = subprocess.run(["git", "-C", repo, "cat-file", "--batch", "--buffer"],
                         input=("\n".join(shas) + "\n").encode(),
                         capture_output=True).stdout
    blobs, pos = {}, 0
    while pos < len(out):
        nl = out.find(b"\n", pos)
        fields = out[pos:nl].decode("utf-8", "replace").split() if nl > 0 else []
        if len(fields) != 3:
            break
        kind, size = fields[1], int(fields[2])
        if kind == "blob" and size <= max_blob:
            blobs[f"history:{fields[0][:10]}"] = out[nl + 1:nl + 1 + size].decode(
                "utf-8", "replace")
        pos = nl + 1 + size + 1
    return blobs


def triage_ticket(ticket, args, cache):
    """Triage one ticket. Returns (code, source, rules sha, rows).

    `cache` carries what the batch shares: the tracked tree (read on first use), the
    live values, each run's corpus, each masking.py revision, and the history index.
    All of it is the same evidence however many tickets were filed off it, and
    re-reading any of it per ticket is what made this a worker's hour.
    """
    live = cache.live
    source, created_at = resolve(ticket, args.source, args.created_at)
    if not source or not (created_at or args.rules_sha):
        print(f"wq#{ticket}: cannot resolve the source run or date the alarm; "
              f"pass --source and --created-at", file=sys.stderr)
        return EXIT_UNRESOLVED, source, None, []

    if source not in cache.corpora:
        cache.corpora[source] = mask_replay.corpus(source)
    items = cache.corpora[source]
    if not items:
        print(f"wq#{source}: no run artefacts under {mask_replay.RUNS_ROOT} — nothing "
              f"to replay", file=sys.stderr)
        return EXIT_UNRESOLVED, source, None, []

    key = args.rules_sha or created_at
    if key not in cache.tables:
        cache.tables[key] = rules_at(created_at, args.repo, args.rules_sha)
    then = cache.tables[key]
    rule_sets = []
    if then:
        rule_sets.append(("created", then[1], then[2]))
    else:
        print(f"wq#{ticket}: no harness/masking.py revision at {created_at}; "
              f"scoring under HEAD only", file=sys.stderr)
    rule_sets.append(("head", mask_replay.CURRENT_PATTERNS, True))

    sha = then[0] if then else None
    tracked = cache.tracked()
    if not control(rule_sets, tracked, live):
        print("CONTROL FAILED: a planted ghp_+36 was not reported UNACCOUNTED under "
              "these rules. No verdict is safe to read.", file=sys.stderr)
        return EXIT_CONTROL, source, sha, []

    rows = triage(source, rule_sets, tracked, items, live)
    if exit_code(rows) == EXIT_CLEAN or args.no_history:
        return exit_code(rows), source, sha, rows

    # Only now is the history index worth its minute: every remaining value is one
    # HEAD's tree cannot explain, which is exactly the question an older revision
    # answers. The control is re-run over the widened tree — a bigger tree is a bigger
    # chance to explain a real token away, so the tool has to prove again that it
    # cannot.
    if "text" not in cache.history:
        cache.history["text"] = history_text(args.repo)
    widened = {**tracked, **cache.history["text"]}
    if not control(rule_sets, widened, live):
        print("CONTROL FAILED: a planted ghp_+36 was accounted by the history index. "
              "No verdict is safe to read.", file=sys.stderr)
        return EXIT_CONTROL, source, sha, []
    rows = triage(source, rule_sets, widened, items, live)
    return exit_code(rows), source, sha, rows


def batch_line(ticket, code, source, sha, rows):
    """One ticket, one line: enough to settle a queue row, no value and no context."""
    state = {EXIT_CLEAN: "CLEAN", EXIT_BURN: "ROTATE",
             EXIT_UNRESOLVED: "UNRESOLVED", EXIT_CONTROL: "CONTROL-FAILED"}[code]
    if code in (EXIT_UNRESOLVED, EXIT_CONTROL):
        return f"wq#{ticket} run wq#{source or '?'} {state}"
    fatal = sum(1 for r in rows if r["verdict"] in FATAL)
    rules = ("created@" + sha[:8] + "," if sha else "") + "head"
    return (f"wq#{ticket} run wq#{source} rules={rules} values={len(rows)} "
            f"rotate={fatal} {state}")


def batch_exit(codes):
    """A batch that cannot trust its tool reports that first, then a burn, then a gap."""
    for code in (EXIT_CONTROL, EXIT_BURN, EXIT_UNRESOLVED):
        if code in codes:
            return code
    return EXIT_CLEAN


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ticket", type=int, nargs="+",
                    help="the [rotation] ticket id(s); two or more triage as a batch, "
                         "one summary line each, sharing the tree and every corpus")
    ap.add_argument("--source", type=int,
                    help="ticket id of the run that fired (default: parsed from the "
                         "rotation ticket's title)")
    ap.add_argument("--created-at", help="when the alarm was filed, "
                    "'YYYY-MM-DD HH:MM:SS' (default: the ticket's created_at)")
    ap.add_argument("--rules-sha", help="score under harness/masking.py at this "
                    "commit instead of the one live at --created-at")
    ap.add_argument("--repo", default=mask_replay.REPO_ROOT)
    ap.add_argument("--extra-tracked", action="append",
                    default=list(mask_replay.DOCTRINE_TRACKED))
    ap.add_argument("--json", action="store_true", help="rows as JSON, no report")
    ap.add_argument("--no-history", action="store_true",
                    help="score against HEAD's tree only: do not account a value "
                         "against the revision of a tracked file that held it")
    args = ap.parse_args(argv)

    tree = {}

    def load_tracked():
        if not tree:
            tree["text"] = mask_replay.tracked_text(args.repo, args.extra_tracked)
        return tree["text"]

    cache = types.SimpleNamespace(tracked=load_tracked, corpora={}, tables={},
                                  history={},
                                  live=burn_filter.live_secret_values(os.environ))
    results = []
    for ticket in args.ticket:
        results.append((ticket, *triage_ticket(ticket, args, cache)))

    if len(results) == 1:
        ticket, code, source, sha, rows = results[0]
        if code in (EXIT_UNRESOLVED, EXIT_CONTROL):
            return code
        if args.json:
            json.dump(rows, sys.stdout, indent=2)
            print()
        else:
            print(f"wq#{ticket} -> run wq#{source}; rules: "
                  f"{'created@' + sha[:8] + ', ' if sha else ''}head; control ok")
            report(source, rows)
            # wq#2677: the other half of the question a triage is really asking. The
            # rows above are what the rule read as a credential; these are what it
            # DECLINED to, which until now left nothing behind at all — a suppressed
            # candidate produced no mask, no label and no record, so an overbroad
            # carve-out (wq#2288's lookahead, silent for a whole PR cycle) read as a
            # clean run. Printed after the verdict and never folded into it: a
            # suppression is audit, not a rotation.
            mask_replay.report_suppressed(source,
                                          mask_replay.suppressed_records(source))
        return code

    if args.json:
        json.dump([{"ticket": t, "source": s, "exit": c, "rules_sha": sha, "rows": rows}
                   for t, c, s, sha, rows in results], sys.stdout, indent=2)
        print()
    else:
        for ticket, code, source, sha, rows in results:
            print(batch_line(ticket, code, source, sha, rows))
        tally = [sum(1 for r in results if r[1] == code)
                 for code in (EXIT_CLEAN, EXIT_BURN, EXIT_UNRESOLVED, EXIT_CONTROL)]
        print(f"\n{len(results)} tickets: {tally[0]} clean, {tally[1]} to rotate, "
              f"{tally[2]} unresolved, {tally[3]} control-failed")
    return batch_exit({r[1] for r in results})


if __name__ == "__main__":
    sys.exit(main())
