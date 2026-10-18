#!/usr/bin/env python3
"""recipe_crystallizer.py — evolving-agents crystallize/freeze pattern for fworker
dispatch (wq#4235, from the wq#4211 dissection, research/WQ4211_EVOLVING_AGENTS_EVAL.md
§2 "adoptable gap": score (ticket-class x worker-profile) cells and let a cell that
keeps winning pin itself, instead of every ticket re-running router.assign's full
archetype-discovery chain from zero).

Model: a "cell" is (ticket_type, model, prompt_template). Every outcome recorded
against a cell bumps its consecutive-success streak on a win and zeroes it on a
loss. Once a cell's streak reaches FREEZE_THRESHOLD, that cell becomes the frozen
recipe for its ticket_type — dispatcher.candidate_model() hands matching tickets
straight to it instead of calling router.assign (step 3). A single failure on a
frozen cell unfreezes it immediately, resetting its streak to 0 (step 4): a recipe
that just broke is the one case where retrying the same path is actively wrong, so
there is no decay/grace window, unlike e.g. circuit_breaker.py's windowed trip count.

Ticket-type classification starts deliberately narrow (the ticket's own "start
with" list): code-refactor, test-fix, config-edit. Anything that doesn't match one
of those three keyword rubrics classifies as None — no cell, no tracking, no
recipe. A winning streak on a ticket class rare enough to not have its own rubric
entry would be noise dressed up as a pattern, not a recipe.

wq#4288: the primary store is now the fleet_memory `recipes` table behind
harness/recipe_store.py (same public API); dispatcher and fworker call that, and it
falls back to this module's JSON file when PostgreSQL is unreachable. This file
still owns classification, the threshold, and the JSON fallback store.

Storage is one JSON file, read-modify-write under an exclusive flock on the file
itself (same seam as harness/issue_log_append.py's row-allocation lock — multiple
fworker instances across worktrees call record_outcome() concurrently), written in
place on the locked fd: truncate to 0, write, fsync. Deliberately NOT
foreman_dispatcher.save_state()'s tmp+rename (wq#4245 flagged the docstring as
claiming otherwise, wq#4254 corrected the docstring rather than the code): a rename
swaps the inode out from under every other holder of this flock, so the lock would
stop serialising anything. load_store()'s unlocked read already fails open to
"nothing frozen" if it catches the truncated moment.
Stdlib-only, fail-open: every public function swallows I/O/JSON errors and
returns the caller's safe default rather than raising, because losing a recipe
update must never cost a ticket its done/block outcome (fworker.py's
record_recipe_outcome() wrapper depends on this).
"""

import contextlib
import fcntl
import json
import os
import re
import time

FOREMAN_ROOT = os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")
STORE_PATH = os.environ.get("FOREMAN_RECIPE_STORE",
                            os.path.join(FOREMAN_ROOT, "dispatcher", "recipes.json"))
# wq#4288: 5, per the spec and evolving-agents-dissection.md R2 ("N times consecutively
# (e.g., N=5)"). wq#4235 shipped 3, and config-edit froze live at streak 3 before
# this was caught; reconcile_freezes() below releases freezes made under a lower bar.
FREEZE_THRESHOLD = int(os.environ.get("FOREMAN_RECIPE_FREEZE_THRESHOLD", "5"))

# wq#4235 step 4: "Start with: code-refactor, test-fix, config-edit." Order matters —
# first rubric that matches wins, so the more specific signals (test-fix, config-edit)
# are checked ahead of the broader code-refactor wording.
_RUBRIC = (
    ("test-fix", (
        r"\bflaky test\b", r"\bfailing test", r"\btest(s)? (is |are )?fail",
        r"\bfix(es|ing)? (a |the )?(failing |broken |flaky )?test",
        r"\bbroken test\b", r"\bre-?enable (a |the )?(skipped|disabled) test",
    )),
    ("config-edit", (
        r"\bconfig(uration)?\b", r"\bsettings\.json\b", r"\byaml\b",
        r"\bbump(ing)? .*(version|pin)\b", r"\benv(ironment)? var",
        r"\.(ya?ml|toml|ini|env)\b",
    )),
    ("code-refactor", (
        r"\brefactor", r"\bclean ?up\b", r"\brestructur", r"\bextract (a |the )?(function|method)",
        r"\bsimplify\b", r"\bdedupe\b", r"\bde-duplicate\b",
    )),
)

TICKET_TYPES = tuple(t for t, _ in _RUBRIC)


def classify_ticket_type(title, detail=""):
    """First-match-wins over _RUBRIC; None when nothing in the starter set hits —
    the caller's cue to skip crystallization entirely for this ticket."""
    text = f"{title or ''}\n{detail or ''}".lower()
    for ticket_type, patterns in _RUBRIC:
        for pat in patterns:
            if re.search(pat, text):
                return ticket_type
    return None


def _empty_store():
    return {"cells": {}, "frozen": {}}


def _load_locked(fh):
    fh.seek(0)
    raw = fh.read()
    if not raw.strip():
        return _empty_store()
    try:
        data = json.loads(raw)
    except ValueError:
        return _empty_store()
    if not isinstance(data, dict):
        return _empty_store()
    data.setdefault("cells", {})
    data.setdefault("frozen", {})
    return data


def _save_locked(fh, store):
    fh.seek(0)
    fh.truncate()
    fh.write(json.dumps(store, indent=1, sort_keys=True))
    fh.flush()
    os.fsync(fh.fileno())


@contextlib.contextmanager
def _open_store_for_write():
    d = os.path.dirname(STORE_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(STORE_PATH, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield fh
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _cell_key(model, prompt_template):
    return f"{model}::{prompt_template}"


def load_store():
    """Read-only snapshot. Fails open to an empty store on any I/O/JSON error —
    a corrupt or missing file reads as "nothing frozen yet", never as an error
    the caller must handle."""
    try:
        with open(STORE_PATH, encoding="utf-8") as fh:
            return _load_locked(fh)
    except OSError:
        return _empty_store()


def lookup_recipe(ticket_type):
    """The frozen {"model", "prompt_template", "streak_at_freeze", "frozen_at"} dict
    for this ticket_type, or None when nothing is frozen for it (including when
    ticket_type itself is None/untracked)."""
    if not ticket_type:
        return None
    return load_store().get("frozen", {}).get(ticket_type)


def is_frozen(ticket_type):
    return lookup_recipe(ticket_type) is not None


def record_outcome(ticket_type, model, prompt_template, success):
    """Update the (ticket_type, model, prompt_template) cell with one attempt's
    outcome. No-op (returns None) when ticket_type isn't in the starter set, or
    when model/prompt_template is empty — there is nothing to pin a recipe to.

    Returns the ticket_type's resulting state: the frozen-recipe dict if this call
    froze or kept a recipe frozen, else the in-progress cell dict, else None.
    Fails open (returns None, no store mutation) on any I/O error — a dropped
    counter update is strictly better than a broken dispatch or close path."""
    if ticket_type not in TICKET_TYPES or not model or not prompt_template:
        return None
    key = _cell_key(model, prompt_template)
    try:
        with _open_store_for_write() as fh:
            store = _load_locked(fh)
            cells = store["cells"].setdefault(ticket_type, {})
            cell = cells.setdefault(
                key, {"model": model, "prompt_template": prompt_template,
                      "streak": 0, "successes": 0, "failures": 0})
            frozen = store["frozen"].get(ticket_type)
            if frozen and frozen["model"] == model and frozen["prompt_template"] == prompt_template:
                if success:
                    cell["successes"] += 1
                    result = dict(frozen)
                else:
                    # wq#4235 step 4: one failure unfreezes immediately — no grace window.
                    cell["failures"] += 1
                    cell["streak"] = 0
                    del store["frozen"][ticket_type]
                    result = dict(cell)
            else:
                if success:
                    cell["successes"] += 1
                    cell["streak"] += 1
                else:
                    cell["failures"] += 1
                    cell["streak"] = 0
                if cell["streak"] >= FREEZE_THRESHOLD and not frozen:
                    frozen_recipe = {
                        "model": model, "prompt_template": prompt_template,
                        "streak_at_freeze": cell["streak"], "frozen_at": time.time(),
                    }
                    store["frozen"][ticket_type] = frozen_recipe
                    result = dict(frozen_recipe)
                else:
                    result = dict(cell)
            _save_locked(fh, store)
            return result
    except OSError:
        return None


def reconcile_freezes(threshold=None, dry_run=False):
    """wq#4288 migration: unfreeze every recipe whose cell streak is below `threshold`
    (default FREEZE_THRESHOLD), under the same store flock record_outcome() takes, so
    it cannot interleave with a concurrent fworker write. The cell itself is left
    alone, streak included, so the next win(s) re-freeze it once the streak reaches
    the threshold rather than restarting from 0. Returns {ticket_type: released
    recipe}. Unlike the runtime API this raises on I/O errors: it is an operator
    migration, and a silent no-op would read as "nothing needed releasing"."""
    threshold = FREEZE_THRESHOLD if threshold is None else threshold
    released = {}
    with _open_store_for_write() as fh:
        store = _load_locked(fh)
        for ticket_type, recipe in list(store["frozen"].items()):
            cells = store["cells"].setdefault(ticket_type, {})
            key = _cell_key(recipe["model"], recipe["prompt_template"])
            streak = recipe.get("streak_at_freeze", 0)
            cell = cells.setdefault(key, {
                "model": recipe["model"], "prompt_template": recipe["prompt_template"],
                "streak": streak, "successes": streak, "failures": 0})
            if cell["streak"] < threshold:
                del store["frozen"][ticket_type]
                released[ticket_type] = dict(recipe)
        if released and not dry_run:
            _save_locked(fh, store)
    return released


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("reconcile", help="unfreeze recipes frozen below the threshold")
    rec.add_argument("--threshold", type=int, default=None)
    rec.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    released = reconcile_freezes(args.threshold, dry_run=args.dry_run)
    print(json.dumps({"store": STORE_PATH, "dry_run": args.dry_run,
                      "threshold": args.threshold or FREEZE_THRESHOLD,
                      "released": released}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
