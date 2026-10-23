"""instincts_render unit tests (wq#959) — tier order, audiences, fail-open contract,
whole-rule budget cuts, and fworker prompt injection. No claude invocation."""
import os
import pytest
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import instincts_render as ir

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CANONICAL = os.path.join(REPO_ROOT, "ops", "wq959-instincts-tiering", "instincts.yaml")

SCHEMA2 = """\
schema: 2
rules:
  - {id: hard-a, tier: 0, added: 2026-08-22, source: x, text: "HARD A"}
  - {id: doc-b, tier: 1, added: 2026-08-22, source: x, text: "DOCTRINE B"}
  - {id: dom-c, tier: 2, added: 2026-08-22, source: x, trigger: "quest work",
     playbook: /tmp/nonexistent-pb.md, text: "DOMAIN C pointer"}
  - {id: hard-d, tier: 0, added: 2026-08-22, source: x, text: "HARD D"}
"""

SCHEMA1 = 'rules:\n  - "old flat rule one"\n  - "old flat rule two"\n'


def _write(tmp_path, content):
    p = tmp_path / "instincts.yaml"
    p.write_text(content)
    return str(p)


def test_worker_gets_tier0_only_in_order(tmp_path):
    out = ir.render("worker", _write(tmp_path, SCHEMA2))
    assert "HARD A" in out and "HARD D" in out
    assert "DOCTRINE B" not in out and "DOMAIN C" not in out
    assert out.startswith(ir.TIER0_HEADER)
    assert out.index("HARD A") < out.index("HARD D")


def test_core_order_is_tier0_first_then_doctrine_then_index(tmp_path):
    out = ir.render("core", _write(tmp_path, SCHEMA2))
    assert out.index(ir.TIER0_HEADER) < out.index(ir.TIER1_HEADER) < out.index(ir.TIER2_HEADER)
    assert out.index("HARD A") < out.index("DOCTRINE B") < out.index("DOMAIN C")
    assert "[quest work]" in out


def test_boot_tier2_is_titles_only_no_rule_text(tmp_path):
    out = ir.render("boot", _write(tmp_path, SCHEMA2))
    assert out.index(ir.TIER0_HEADER) < out.index(ir.TIER1_HEADER) < out.index(ir.TIER2_HEADER)
    assert "HARD A" in out and "DOCTRINE B" in out
    assert "[quest work]" in out
    assert "DOMAIN C pointer" not in out


def test_schema1_flat_list_fails_open(tmp_path):
    path = _write(tmp_path, SCHEMA1)
    assert ir.load_rules(path) is None
    assert ir.render("worker", path) == ""
    assert ir.render("core", path) == ""


def test_missing_file_fails_open(tmp_path):
    assert ir.tier0_block(str(tmp_path / "nope.yaml")) == ""


def test_budget_cut_drops_whole_rules_never_mid_rule(tmp_path):
    big = "schema: 2\nrules:\n" + "".join(
        '  - {id: r%d, tier: 0, added: x, source: x, text: "%s"}\n' % (i, "word " * 100)
        for i in range(10)
    )
    warnings = []
    out = ir.tier0_block(_write(tmp_path, big), budget=300, warn=warnings.append)
    assert warnings, "over-budget cut must warn"
    body = [l for l in out.splitlines()[1:]]
    for line in body:
        assert line.rstrip().endswith("word"), "rule was cut mid-text: %r" % line[-20:]


def test_canonical_corpus_is_clean_and_within_budget():
    errs = [e for e in ir.check(CANONICAL) if "playbook missing on disk" not in e]
    assert errs == [], errs
    rules = ir.load_rules(CANONICAL)
    t0 = [r for r in rules if r["tier"] == 0]
    assert len(t0) == 7
    block = ir.tier0_block(CANONICAL)
    assert ir._est_tokens(block) <= ir.TIER0_TOKEN_BUDGET
    assert "THOR" in block and "FULL AUTONOMY" in block and "MASK BEFORE PRINT" in block


def test_fworker_prompt_injection_and_fail_open(tmp_path, monkeypatch):
    monkeypatch.setenv("FOREMAN_INSTINCTS_PATH", _write(tmp_path, SCHEMA2))
    monkeypatch.setattr(ir, "INSTINCTS_PATH", str(tmp_path / "instincts.yaml"))
    import fworker
    prompt = fworker.load_worker_prompt(999, {"title": "t", "detail": "d"}, "/tmp/wt")
    assert "HARD A" in prompt and "DOCTRINE B" not in prompt
    assert prompt.index("HARD A") < prompt.index("RULES:")
    assert "{{INSTINCTS_TIER0}}" not in prompt
    # fail-open: schema-1 live file -> placeholder removed cleanly, no crash
    monkeypatch.setattr(ir, "INSTINCTS_PATH", _write(tmp_path, SCHEMA1))
    prompt = fworker.load_worker_prompt(999, {"title": "t", "detail": "d"}, "/tmp/wt")
    assert "{{INSTINCTS_TIER0}}" not in prompt
    assert "old flat rule" not in prompt


def test_cli_audience_boot_is_callable_and_smaller_than_core():
    # wq#4582: claude_context.sh line 56 is repointed (ops/wq4540 install.sh step 4)
    # to shell out to `python3 harness/instincts_render.py --audience boot`. Pin the
    # CLI invocation shape the call site depends on — module-API tests above cannot
    # catch a renamed flag or a nonzero exit — and the savings mechanism (boot output
    # strictly smaller than core's on the committed canonical corpus).
    import subprocess
    script = os.path.join(os.path.dirname(__file__), "..", "instincts_render.py")
    runs = {
        aud: subprocess.run(
            [sys.executable, script, "--audience", aud, "--file", CANONICAL],
            capture_output=True, text=True,
        )
        for aud in ("boot", "core")
    }
    for aud, r in runs.items():
        assert r.returncode == 0, (aud, r.stderr)
        assert r.stdout.strip(), "--audience %s emitted nothing" % aud
    assert runs["boot"].stdout.startswith(ir.TIER0_HEADER)
    assert len(runs["boot"].stdout) < len(runs["core"].stdout)


LIVE_INSTINCTS = "/home/lumen/instincts.yaml"


@pytest.mark.skipif(not os.path.exists(LIVE_INSTINCTS), reason="live instincts.yaml not on this host")
def test_live_tier0_fits_budget_no_rule_dropped():
    # wq#3660: the live file overflowed the cap and dropped fabricated-evidence
    # from every fworker prompt with only a journal warning.
    rules = ir.load_rules(LIVE_INSTINCTS)
    warnings = []
    block = ir.tier0_block(LIVE_INSTINCTS, warn=warnings.append)
    assert warnings == [], warnings
    n_t0 = len([r for r in rules if r["tier"] == 0])
    assert len([l for l in block.splitlines()[1:]]) == n_t0
