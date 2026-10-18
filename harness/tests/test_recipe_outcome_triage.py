"""wq#4254 (judgment on wq#4245's CONTRADICT of wq#4235): handle_failure() must not
score a terminal block against the (model, prompt_template) recipe cell when the
block's triage says nothing about model competence.

wq#4235 wired record_recipe_outcome(success=False) into handle_failure()
unconditionally, so a REAL_BLOCK (the worker correctly self-blocked on a Frank-only
decision — the documented good path), a CAPABILITY_DENIED (sandbox policy wall) and a
HARNESS_DEFECT (worktree/repo mismatch) each unfroze a working frozen recipe. wq#4245
reproduced the unfreeze live; no test covered either fworker call site's
triage-sensitivity.

These drive the REAL handle_failure() against a REAL recipe_crystallizer store on a
scratch path (only the terminal-failure record, wq calls and ntfy are stubbed, same
shape as test_local_rung_escalation.py's `stubbed` fixture) so the assertions are about
the store the dispatcher actually reads. The GENUINE_BUG / LIMIT / UNKNOWN cases are the
falsifiers: a fix that simply stopped recording failures would pass the exempt cases and
fail those.
"""
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import pytest  # noqa: E402

import failure_record  # noqa: E402
import fworker  # noqa: E402
import recipe_crystallizer  # noqa: E402

MODEL = "claude-sonnet-5"
TEMPLATE = "standard-engineering"
# classify_ticket_type() -> "test-fix"; the ticket class the frozen recipe is pinned to.
TICKET = {"status": "open", "title": "fix the failing test in test_dispatcher.py",
          "detail": "", "lane": "build", "needs": ""}


class _Args:
    model = MODEL
    repo = "/nowhere"
    archetype = TEMPLATE


def _rec(triage):
    return ({"block_class": "WORK_BLOCKED", "triage": triage, "summary": "x",
             "unreachable_paths": []}, "/tmp/fake-failure.json")


@pytest.fixture(autouse=True)
def scratch_store(tmp_path, monkeypatch):
    monkeypatch.setattr(recipe_crystallizer, "STORE_PATH", str(tmp_path / "recipes.json"))
    monkeypatch.setattr(recipe_crystallizer, "FREEZE_THRESHOLD", 3)


@pytest.fixture
def stubbed(monkeypatch):
    calls = {"block": [], "ntfy": []}
    monkeypatch.setattr(fworker, "write_prior_attempt_notes", lambda *a, **k: None)
    monkeypatch.setattr(fworker, "wq_block", lambda t, r: calls["block"].append((t, r)))
    monkeypatch.setattr(fworker, "ntfy", lambda *a, **k: calls["ntfy"].append(a))
    monkeypatch.setattr(fworker.capability_proposal, "file_denied_row", lambda *a, **k: False)
    monkeypatch.setattr(fworker, "wq_show", lambda t: dict(TICKET))
    monkeypatch.setattr(fworker, "charged_attempt_count",
                        lambda *a, **k: fworker.MAX_ATTEMPTS)
    monkeypatch.setattr(fworker, "do_attempt",
                        lambda *a, **k: pytest.fail("exhausted attempts must not retry"))
    return calls


def _freeze_a_recipe():
    for _ in range(3):
        recipe_crystallizer.record_outcome("test-fix", MODEL, TEMPLATE, True)
    frozen = recipe_crystallizer.lookup_recipe("test-fix")
    assert frozen and frozen["model"] == MODEL, "precondition: recipe is frozen"
    return frozen


def _block_with(monkeypatch, triage, ticket_id, tmp_path):
    monkeypatch.setattr(fworker, "record_terminal_failure", lambda *a, **k: _rec(triage))
    return fworker.handle_failure(_Args(), ticket_id, 1, "REFUSED", None, [], str(tmp_path))


@pytest.mark.parametrize("triage", sorted(failure_record.TRIAGE_NON_COMPETENCE))
def test_non_competence_triage_leaves_a_frozen_recipe_frozen(triage, stubbed, monkeypatch,
                                                              tmp_path):
    frozen = _freeze_a_recipe()

    rc = _block_with(monkeypatch, triage, 5101, tmp_path)

    assert rc == fworker.EXIT_BLOCKED
    assert recipe_crystallizer.lookup_recipe("test-fix") == frozen, \
        f"{triage} says nothing about the model's competence at this ticket class"


@pytest.mark.parametrize("triage", sorted(failure_record.TRIAGE_NON_COMPETENCE))
def test_non_competence_triage_does_not_touch_the_cell_counters(triage, stubbed,
                                                                 monkeypatch, tmp_path):
    _freeze_a_recipe()
    before = recipe_crystallizer.load_store()["cells"]["test-fix"]

    _block_with(monkeypatch, triage, 5102, tmp_path)

    assert recipe_crystallizer.load_store()["cells"]["test-fix"] == before, \
        "an exempt block is not a loss AND not a win — the cell is simply not written"


@pytest.mark.parametrize("triage", [failure_record.TRIAGE_GENUINE_BUG,
                                    failure_record.TRIAGE_LIMIT,
                                    failure_record.TRIAGE_UNKNOWN])
def test_competence_bearing_triage_still_unfreezes(triage, stubbed, monkeypatch, tmp_path):
    _freeze_a_recipe()

    _block_with(monkeypatch, triage, 5103, tmp_path)

    assert recipe_crystallizer.lookup_recipe("test-fix") is None, \
        f"{triage} is a real signal about this cell — wq#4235's unfreeze must still fire"
    cell = recipe_crystallizer.load_store()["cells"]["test-fix"][f"{MODEL}::{TEMPLATE}"]
    assert cell["streak"] == 0 and cell["failures"] == 1


def test_untracked_ticket_class_is_unaffected_either_way(stubbed, monkeypatch, tmp_path):
    monkeypatch.setattr(fworker, "wq_show",
                        lambda t: {"status": "open", "title": "stand up a Grafana board",
                                   "detail": "", "lane": "build", "needs": ""})

    _block_with(monkeypatch, failure_record.TRIAGE_GENUINE_BUG, 5104, tmp_path)

    assert recipe_crystallizer.load_store()["cells"] == {}


# ---------- record_recipe_outcome() unit-level (the gate itself) ----------

def test_success_path_records_with_no_triage_argument():
    """reconcile()'s call site (fworker.py:4377) passes no triage — it must still count."""
    fworker.record_recipe_outcome(dict(TICKET), _Args(), success=True)

    cell = recipe_crystallizer.load_store()["cells"]["test-fix"][f"{MODEL}::{TEMPLATE}"]
    assert cell["streak"] == 1 and cell["successes"] == 1


def test_an_exempt_failure_cannot_block_a_later_freeze():
    """The streak survives the exempt block: two wins, a REAL_BLOCK, a third win still
    freezes. Pre-fix the REAL_BLOCK zeroed the streak and the recipe never crystallized."""
    for _ in range(2):
        fworker.record_recipe_outcome(dict(TICKET), _Args(), success=True)
    fworker.record_recipe_outcome(dict(TICKET), _Args(), success=False,
                                  triage=failure_record.TRIAGE_REAL_BLOCK)
    fworker.record_recipe_outcome(dict(TICKET), _Args(), success=True)

    frozen = recipe_crystallizer.lookup_recipe("test-fix")
    assert frozen and frozen["streak_at_freeze"] == 3
