#!/usr/bin/env python3
"""test_presentation_stability.py — acceptance for wq#2086 (proposal d653cc13c6a3).

Network-free. The one test that touches the live tree imports the REAL
/home/lumen/minerva/minerva_verify.py with its judge, embedder and advisory layers
monkeypatched, purely to prove the probe's central safety property: verify() appends to
verification_log on every call, and the probe must not let those appends reach the live
corpus — otherwise the probe's own perturbed variants become next week's "already-scored
eval items". That test skips (never fails) when the minerva tree is not readable.

Run: pytest -q ops/wq2086-presentation-stability/test_presentation_stability.py
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MINERVA_DIR = os.environ.get("PSTAB_MINERVA_DIR", "/home/lumen/minerva")

_spec = importlib.util.spec_from_file_location(
    "pstab", os.path.join(HERE, "minerva_presentation_stability.py"))
pstab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pstab)

ITEM = ("Minerva evaluates proposals via minerva-eval.timer (minerva_eval.py) and scores "
        "them with minerva_verify.py; coverage is approximately 0.5 on wq#2086. The judge "
        "rung does not test surface stability, and no harness reads "
        "/home/lumen/minerva/minerva.db for a second opinion. Full details live at "
        "https://example.org/a for review.")

SCHEMA = """CREATE TABLE verification_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, input_text TEXT, context_ref TEXT,
    citation_score REAL, judge_score REAL, final_score REAL, passed INTEGER,
    rejected_reason TEXT, model TEXT, ts TEXT, judge_rationale TEXT, error_kind TEXT,
    mode TEXT, n_claims INTEGER, claims_entailed INTEGER, span_confidence TEXT,
    judge_ms TEXT)"""


def scratch_db(rows):
    path = os.path.join(tempfile.mkdtemp(prefix="pstab-test-"), "minerva.db")
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    conn.executemany(
        "INSERT INTO verification_log (subject, input_text, context_ref, model, mode, "
        "passed, final_score, citation_score, judge_score, error_kind, ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


def row(subject="proposal:x", answer=ITEM, context="F" * 400, model="deepseek-v4-flash",
        mode="semantic", passed=1, final=0.8, err=None):
    return (subject, answer, context, model, mode, passed, final, 0.7, 0.8, err,
            "2026-09-19T00:00:00+00:00")


# ------------------------------------------------------------------ gate tiers

def test_selftest_passes():
    assert pstab.selftest() == 0


@pytest.mark.parametrize("mutate,expect", [
    (lambda t: t.replace("0.5", "0.9"), "evidence_differs"),
    (lambda t: t.replace("https://example.org/a", "https://example.org/b"), "evidence_differs"),
    (lambda t: t.replace("wq#2086", "wq#2087"), "evidence_differs"),
    (lambda t: t.replace("/home/lumen/minerva/minerva.db", "/tmp/minerva.db"), "evidence_differs"),
    (lambda t: t.replace("does not ", ""), "content_differs"),          # dropped negation
    (lambda t: t.replace("does not test", "may not always test"), "content_differs"),
    (lambda t: t.replace("no harness reads", "few harnesses read"), "content_differs"),
    (lambda t: t + " Every rung agrees.", "content_differs"),           # added claim
])
def test_gate_rejects_competence_changes(mutate, expect):
    ok, why = pstab.competence_preserved(ITEM, mutate(ITEM))
    assert not ok and why.startswith(expect), why


def test_gate_rejects_a_no_op_variant():
    assert pstab.competence_preserved(ITEM, ITEM) == (False, "identical")


def test_declared_synonyms_round_trip_through_the_gate():
    v = pstab.t_synonym(ITEM, 0)
    assert "doesn't" in v and "about 0.5" in v
    assert pstab.competence_preserved(ITEM, v)[0]


def test_every_transform_is_deterministic_and_gate_clean():
    admitted, rejected = pstab.build_variants(ITEM, 42, len(pstab.TRANSFORMS))
    assert [n for n, _ in admitted] == [n for n, _ in
                                        pstab.build_variants(ITEM, 42, len(pstab.TRANSFORMS))[0]]
    assert len(admitted) >= 3, (admitted, rejected)
    for name, text in admitted:
        assert pstab.competence_preserved(ITEM, text)[0], name


def test_reorder_declines_on_back_references():
    text = ("Minerva scores proposals on rung 7. None of these currently test surface "
            "stability. It also skips formatting.")
    assert pstab.t_reorder(text, 3) == text


# ------------------------------------------------------------------ item input

def test_load_items_skips_unreplayable_and_probe_authored_rows():
    db = scratch_db([
        row(subject="proposal:good"),
        row(subject="pstab:99:reflow"),                      # probe's own output
        row(subject="proposal:short-context", context="too short"),
        row(subject="proposal:short-answer", answer="tiny"),
        row(subject="proposal:errored", err="judge_err:ReadTimeout"),
    ])
    got = [i["subject"] for i in pstab.load_items(db, limit=10)]
    assert got == ["proposal:good"], got


def test_load_items_honours_pinned_ids_and_opens_read_only():
    db = scratch_db([row(subject="proposal:a"), row(subject="proposal:b")])
    ids = [i["id"] for i in pstab.load_items(db, limit=10)]
    assert [i["subject"] for i in pstab.load_items(db, 10, ids=[ids[-1]])] == ["proposal:a"]
    uri = "file:%s?mode=ro" % db
    with sqlite3.connect(uri, uri=True) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM verification_log")


# --------------------------------------------------------- flips and budgeting

def fake_score(flip_on=(), self_flip=False, error=None):
    state = {"baselines": 0}

    def score(answer, context, mode, rung, subject):
        kind = subject.rsplit(":", 1)[-1]
        if kind == "control" and self_flip:
            return {"passed": False, "final": 0.2, "citation": 0.5, "judge": 0.5,
                    "error": error}
        flip = kind in flip_on
        state["baselines"] += kind == "baseline"
        return {"passed": not flip, "final": 0.3 if flip else 0.9, "citation": 0.6,
                "judge": 0.7, "error": error}
    return score


def items_for(n=1):
    return [{"id": 100 + i, "subject": "proposal:%d" % i, "input_text": ITEM,
             "context_ref": "F" * 400, "model": "deepseek-v4-flash", "mode": "semantic",
             "passed": 1, "final_score": 0.8} for i in range(n)]


def test_excess_flip_rate_subtracts_the_rungs_own_noise():
    noisy = pstab.probe(items_for(2), fake_score(flip_on=("reflow",), self_flip=True),
                        variants=2, max_scores=99, rungs=["r"])[0]["r"]
    assert noisy["flip_rate"] == 0.5 and noisy["self_flip_rate"] == 1.0
    assert noisy["excess_flip_rate"] == -0.5          # every "flip" is rung noise

    clean = pstab.probe(items_for(2), fake_score(flip_on=("reflow",)),
                        variants=2, max_scores=99, rungs=["r"])[0]["r"]
    assert clean["flip_rate"] == 0.5 and clean["self_flip_rate"] == 0.0
    assert clean["excess_flip_rate"] == 0.5 and clean["max_abs_delta_final"] == 0.6


def test_default_rung_is_the_production_judge_not_the_recorded_one():
    met = pstab.probe(items_for(1), fake_score(), variants=1, max_scores=99)[0]
    assert list(met) == [pstab.PROD_RUNG] != ["deepseek-v4-flash"]


def test_budget_cap_stops_before_overspending():
    met, recs, note = pstab.probe(items_for(4), fake_score(), variants=2, max_scores=9,
                                 rungs=["r"])
    assert met["r"]["items_scored"] == 2 and "PSTAB_MAX_SCORES=9" in note
    assert len(recs) == 2                            # a partial item is never recorded


def test_errors_are_counted_not_swallowed():
    met = pstab.probe(items_for(1), fake_score(error="judge_err:ReadTimeout"),
                      variants=2, max_scores=99, rungs=["r"])[0]["r"]
    assert met["errors"] == 4                        # baseline + control + 2 variants


def test_dry_run_scores_nothing_but_reports_gate_admissions():
    met, recs, _ = pstab.probe(items_for(2), None, variants=2, max_scores=0, rungs=["r"],
                              dry_run=True)
    assert met["r"]["variants_admitted"] == 4 and met["r"]["variants_scored"] == 0
    assert all("baseline" not in r for r in recs)


# ---------------------------------------------------------------- notification

def test_ntfy_only_fires_over_threshold_or_on_a_dead_run():
    met, recs, _ = pstab.probe(items_for(2), fake_score(flip_on=("reflow",)), variants=2,
                               max_scores=99, rungs=["r"])
    assert pstab.notify(met, recs, None, threshold=0.9, ntfy_bin="/bin/true") is None
    assert "presentation-sensitive" in pstab.notify(met, recs, None, threshold=0.4,
                                                    ntfy_bin="/bin/true")
    dead, drecs, _ = pstab.probe(items_for(1), fake_score(error="judge_err"), variants=2,
                                 max_scores=99, rungs=["r"])
    assert "scored nothing" in pstab.notify(dead, drecs, None, ntfy_bin="/bin/true")


# -------------------------------------------- the live corpus must stay untouched

def test_probe_never_appends_to_the_live_verification_log():
    """verify() INSERTs into minerva_verify.DB on every call (minerva_verify.py, the
    `INSERT INTO verification_log` after the advisory layers). make_minerva_scorer must
    redirect that at a throwaway DB, or the probe poisons its own item source."""
    live = os.path.join(MINERVA_DIR, "minerva.db")
    if not (os.access(os.path.join(MINERVA_DIR, "minerva_verify.py"), os.R_OK)
            and os.access(live, os.R_OK)):
        pytest.skip("minerva tree not readable here")

    def count():
        uri = "file:%s?mode=ro" % live
        with sqlite3.connect(uri, uri=True) as conn:
            return conn.execute("SELECT COUNT(*) FROM verification_log").fetchone()[0]

    before = count()
    log_db = os.path.join(tempfile.mkdtemp(prefix="pstab-live-"), "throwaway.db")
    score = pstab.make_minerva_scorer(MINERVA_DIR, log_db)
    import minerva_verify as mv
    assert mv.DB == log_db, "DB redirect is the whole safety property"
    assert os.environ["VERIFY_ALERT"] == "0", "probe errors must not page as prod outages"
    mv.log = lambda *a, **k: None                                  # no live logfile write
    mv.EXTERNAL_ADVISORY_CHECK_ENABLED = False                     # no NVD/GitHub calls
    mv.semantic_citation_score = lambda claims, ctx: (0.8, [], None)
    # **k: verify() passes authority_note= since wq#2460 (7e221f67)
    mv.judge_entailment_uq = lambda claims, ctx, **k: (0.9, ["SUPPORTED"], "ok", None, [1.0])

    out = score(ITEM, "F" * 400, "semantic", "test-rung", "pstab:1:baseline")
    assert out["passed"] is True and out["final"] > 0
    assert mv.JUDGE_MODEL == "test-rung" and mv.JUDGE_MODEL_2 == "", "rung must be pinned"
    with sqlite3.connect(log_db) as conn:
        staged = conn.execute("SELECT subject FROM verification_log").fetchall()
    assert staged == [("pstab:1:baseline",)], staged
    assert count() == before, "the live corpus must not grow"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
