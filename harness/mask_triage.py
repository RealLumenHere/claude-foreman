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
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import burn_filter  # noqa: E402
import mask_replay  # noqa: E402
import masking  # noqa: E402

WQ_DB = os.environ.get("WQ_DB", "/home/lumen/agents/work-queue/queue.db")
MASKING_REL = "harness/masking.py"
SOURCE_RE = re.compile(r"burned credential in wq#(\d+)")
TREE = "<tracked>"  # the one "repo root" classify() is given; the runner ignores it

FATAL = ("UNACCOUNTED", "LIVE")
EXIT_CLEAN, EXIT_BURN, EXIT_UNRESOLVED, EXIT_CONTROL = 0, 1, 2, 3


def ticket_row(ticket_id, db=WQ_DB):
    """(title, created_at) of a queue ticket, or None. Read-only: triage never edits."""
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            return conn.execute("SELECT title, created_at FROM tasks WHERE id=?",
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ticket", type=int, help="the [rotation] ticket id")
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
    args = ap.parse_args(argv)

    row = ticket_row(args.ticket)
    named = SOURCE_RE.search(row[0]) if row else None
    source = args.source or (int(named.group(1)) if named else None)
    created_at = args.created_at or (row[1] if row else None)
    if not source or not (created_at or args.rules_sha):
        print(f"wq#{args.ticket}: cannot resolve the source run or date the alarm; "
              f"pass --source and --created-at", file=sys.stderr)
        return EXIT_UNRESOLVED

    items = mask_replay.corpus(source)
    if not items:
        print(f"wq#{source}: no run artefacts under {mask_replay.RUNS_ROOT} — nothing "
              f"to replay", file=sys.stderr)
        return EXIT_UNRESOLVED

    rule_sets = []
    then = rules_at(created_at, args.repo, args.rules_sha)
    if then:
        rule_sets.append(("created", then[1], then[2]))
    else:
        print(f"wq#{args.ticket}: no harness/masking.py revision at {created_at}; "
              f"scoring under HEAD only", file=sys.stderr)
    rule_sets.append(("head", mask_replay.CURRENT_PATTERNS, True))

    tracked = mask_replay.tracked_text(args.repo, args.extra_tracked)
    live = burn_filter.live_secret_values(os.environ)
    if not control(rule_sets, tracked, live):
        print("CONTROL FAILED: a planted ghp_+36 was not reported UNACCOUNTED under "
              "these rules. No verdict is safe to read.", file=sys.stderr)
        return EXIT_CONTROL

    rows = triage(source, rule_sets, tracked, items, live)
    if args.json:
        json.dump(rows, sys.stdout, indent=2)
        print()
    else:
        print(f"wq#{args.ticket} -> run wq#{source}; rules: "
              f"{'created@' + then[0][:8] + ', ' if then else ''}head; control ok")
        report(source, rows)
    return exit_code(rows)


if __name__ == "__main__":
    sys.exit(main())
