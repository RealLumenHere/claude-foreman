"""wq#617 T3 orchestrator tier — routing, dominated guard, downgrade doctrine, refusal
awareness, verify-pass + judgment policy. Zero Anthropic quota."""
import collections
import copy
import datetime as dt
import itertools
import json
import os
import re
import sqlite3
import subprocess
import sys

import pytest
import yaml

ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ORCH_DIR)
import router  # noqa: E402
import judgment  # noqa: E402
import repo_sweep  # noqa: E402

MANIFEST = os.path.join(ORCH_DIR, "..", "research", "models.yaml")

# BUILD_PLAN §T3 verify: "assign 5 sample tickets across archetypes -> correct (model,effort)"
SAMPLES = [
    ("wire standing checks to systemd timers", "", "fleet", "mechanical-edit", ("claude-sonnet-5-5", "low")),
    ("probe: CI hygiene for lumen-pr", "", "build", "probe-build", ("claude-sonnet-5-5", "medium")),
    ("add conflict guard to lumen-pr", "", "build", "standard-engineering", ("claude-opus-5-5", "high")),
    ("audit: why does lpp-watchdog bypass the guard", "", "pokemon", "rca-investigation", ("claude-opus-5-5", "high")),
    ("design lumen-memoryd new service", "", "memory", "novel-design", ("claude-fable-5-2", "high")),
    ("research pack: agent farm prior art report", "", "research", "research-synthesis", ("claude-opus-5-5", "medium")),
    ("fresh-eyes verify-pass of wq#600 fix", "", "build", "verify-pass", ("claude-sonnet-5-5", "high")),
    ("restart the auth service", "", "fleet-fires", "fire-response", ("claude-opus-5-5", "high")),
]


@pytest.fixture(scope="module")
def m():
    return router.load_manifest(MANIFEST, force=True)


@pytest.fixture(autouse=True)
def cooldown_file(tmp_path, monkeypatch):
    """wq#2133: select() reads runtime/backend_cooldown.json; never the live one. Yields the
    scratch path (absent = nothing cooling down), set for in-process and subprocess callers."""
    path = tmp_path / "backend_cooldown.json"
    monkeypatch.setattr(router.local_capacity, "COOLDOWN_PATH", str(path))
    monkeypatch.setenv("FOREMAN_BACKEND_COOLDOWN", str(path))
    monkeypatch.delenv("FOREMAN_LOCAL_CAPACITY", raising=False)
    return path


def test_manifest_loads_and_self_checks(m):
    assert m.check()
    assert set(router.ARCHETYPES) <= set(m.routing)


# ---- wq#1329 (ISSUE-LOG #127): alias table derived from models.yaml, not hand-maintained

def test_every_yaml_alias_round_trips(m):
    # every models[].alias in the manifest resolves back to that exact model's id —
    # this is the check that would have caught #127 (fable-5-1's alias field existed
    # in the yaml but resolve_alias('fable-5-1') passed it through unresolved because
    # the old hardcoded ALIASES dict was never updated to match).
    with open(MANIFEST) as f:
        raw = yaml.safe_load(f)
    checked = 0
    for model in raw["models"]:
        mid = model["id"]
        alias = model.get("alias")
        if alias:
            assert router.resolve_alias(alias) == mid, \
                f"alias {alias!r} should resolve to {mid!r}"
            checked += 1
        # bare family shorthand (id minus the 'claude-' prefix) must also round-trip —
        # this is what covers fable-5-1 specifically, since its shorthand IS its alias.
        if mid.startswith("claude-"):
            shorthand = mid[len("claude-"):]
            assert router.resolve_alias(shorthand) == mid, \
                f"shorthand {shorthand!r} should resolve to {mid!r}"
            checked += 1
    assert checked > 0, "models.yaml had no aliases to check — test would pass vacuously"


def test_resolve_alias_fable_5_1_specifically(m):
    # the exact regression #127 named: fable-5-1's alias was missing from the old
    # hardcoded dict while fable-5's was present, so only this one silently broke.
    assert router.resolve_alias("fable-5-1") == "claude-fable-5-1"
    assert router.resolve_alias("fable") == "claude-fable-5-2"   # wq#5083: alias follows the current Fable
    assert router.resolve_alias("fable-5") == "claude-fable-5"   # family shorthand still pins 5.0


def test_haiku_alias_moved_to_haiku_5_5_and_fallback_table_agrees(m):
    # wq#3618: the yaml alias and the import-time fallback dict must name the same model
    assert router.resolve_alias("haiku") == "claude-haiku-5-5"
    assert router.resolve_alias("haiku-4-5") == "claude-haiku-4-5"
    for alias in ("haiku", "haiku-5-5", "haiku-4-5"):
        assert router._FALLBACK_ALIASES[alias] == router.resolve_alias(alias)


def test_legacy_aliases_resolve_and_never_shadow_a_live_name(m):
    # wq#4220: legacy_aliases used to be read by nothing while wq#4210's commit claimed
    # it was what kept the retired deepseek name resolving (wq#4217 CONTRADICT).
    assert router.resolve_alias("deepseek-flash") == "deepseek-v4-flash"
    with open(MANIFEST) as f:
        raw = yaml.safe_load(f)
    ids = {model["id"] for model in raw["models"]}
    checked = 0
    for model in raw["models"]:
        for old in model.get("legacy_aliases") or []:
            # a legacy name that some other row claims as its id, or as its live alias,
            # must keep resolving to that row — compatibility never wins over a live name
            expect = old if old in ids else model["id"]
            assert router.resolve_alias(old) == expect, f"legacy alias {old!r}"
            checked += 1
    assert checked > 0, "no legacy_aliases in models.yaml — test would pass vacuously"


def test_legacy_alias_loses_to_a_live_id_and_to_a_declared_alias():
    # synthetic manifest: the same string claimed three ways. _build_aliases is pure,
    # so this probes the precedence rule directly rather than waiting for the live
    # manifest to grow a collision.
    data = {"models": [
        {"id": "retired-name", "status": "LIVE"},                    # a live id ...
        {"id": "keeper", "legacy_aliases": ["retired-name"]},        # ... outranks this
        {"id": "real", "alias": "short"},                            # a declared alias ...
        {"id": "other", "legacy_aliases": ["short", "moved-away"]},  # ... outranks this
    ]}
    aliases = router._build_aliases(data=data)
    assert "retired-name" not in aliases          # falls through to the live id
    assert aliases["short"] == "real"
    assert aliases["moved-away"] == "other"       # uncontested legacy name still maps


@pytest.mark.parametrize("title,detail,lane,arch,expected", SAMPLES)
def test_sample_tickets_route_to_expected_tuple(m, title, detail, lane, arch, expected):
    got_arch, _ = router.classify_archetype(title, detail, lane)
    assert got_arch == arch
    rung, _ = router.select(m, got_arch)
    assert (rung.model, rung.effort) == expected


def test_fleet_fires_lane_overrides_keyword_rubric():
    # wq#717: a fleet-fires title that would otherwise hit rca-investigation's "audit"
    # signal must still route to fire-response — production-state tickets can't be
    # routed around the manual-approval workflow by title wording.
    arch, signals = router.classify_archetype(
        "audit: why is the fleet-agent service down", "root cause investigation",
        "fleet-fires")
    assert arch == "fire-response"
    assert any("fleet-fires" in s for s in signals)


# ---- wq#4633: RESEARCH:/AUDIT: briefs never land on a cheap archetype -----------------

# Pinned copies of the wq#4628 / wq#4630 tickets (2026-10-25) that routed
# mechanical-edit@low off the "how to wire into our fworker dispatch" clause.
WQ4628 = ("RESEARCH: Trigger.dev — durable execution engine for overnight agent batches",
          "Open-source durable execution platform for long-running tasks. Current pain: "
          "overnight fworkers are fire-and-forget Claude chats. Trigger.dev would give us: "
          "durable queuing with automatic retry, checkpoint/resume for multi-hour builds. "
          "Research: self-hosted deployment (Docker), TypeScript SDK integration, how to "
          "wire into our fworker dispatch, can it call Claude API or claude -p, PostgreSQL "
          "state backend (we already have pgvector). Compare to: Temporal, Inngest, BullMQ.")
WQ4630 = ("RESEARCH: LangGraph — stateful agent graphs with checkpoint-to-Postgres",
          "LangGraph models orchestration as a directed graph with built-in checkpointers "
          "to Postgres/SQLite/Redis. Research: how to wire the Postgres checkpointer, "
          "planner/executor split.")
EFFORT_FLOOR = router.EFFORT_LADDER.index("medium")


@pytest.mark.parametrize("ticket", [WQ4628, WQ4630])
@pytest.mark.parametrize("lane", ["build", "fleet", "pilot", "", "research"])
def test_research_title_prefix_forces_research_synthesis_in_any_lane(m, ticket, lane):
    arch, signals = router.classify_archetype(*ticket, lane, m)
    assert arch == "research-synthesis"
    assert any("wq#4633" in s for s in signals)


@pytest.mark.parametrize("title", ["AUDIT: rename sweep of the wire protocol",
                                   "  research : bump the probe CI", "Audit:wire it",
                                   "AUDIT: campaign checkpoint uplift"])
def test_audit_and_case_variants_of_the_prefix(m, title):
    assert router.classify_archetype(title, "", "fleet", m)[0] == "research-synthesis"


def test_prefix_keeps_an_analysis_pick_from_the_rubric(m):
    # an AUDIT: that the rubric already reads as RCA stays RCA (opus@high), not demoted
    arch, signals = router.classify_archetype(
        "AUDIT: why does lpp-watchdog bypass the guard", "", "fleet", m)
    assert arch == "rca-investigation"
    assert any("wq#4633" in s for s in signals)


def test_prefix_must_lead_the_title(m):
    # "research" mid-title is still the wq#778 weak signal, not the forced prefix
    arch, signals = router.classify_archetype("wire the research: dashboard", "", "fleet", m)
    assert arch == "mechanical-edit"
    assert not any("wq#4633" in s for s in signals)


def test_fleet_fires_still_outranks_research_prefix(m):
    assert router.classify_archetype("AUDIT: vault down", "", "fleet-fires", m)[0] == \
        "fire-response"


# wq#4716: pinned copy of wq#4183 as it stood 2026-10-27. It was filed and stamped
# standard-engineering, retitled RESEARCH: afterwards, and assign() kept the stale stamp.
WQ4183 = {"id": 4183, "lane": "research", "model": "claude-sonnet-5", "effort": "high",
          "archetype": "standard-engineering", "subsystems": "",
          "title": "RESEARCH: [limpet] CFP calendar: DEF CON HHV/IoT Village, Hardwear.io, "
                   "REcon, BSides SF — deadlines + draft tool-demo abstracts",
          "detail": "Web-search each 2027 CFP window, produce a dated table + one 150-word "
                    "village/tool-demo abstract. No submission without Frank."}


@pytest.mark.parametrize("stored, title, want", [
    ("standard-engineering", WQ4183["title"], "research-synthesis"),   # the wq#4183 case
    ("mechanical-edit", "audit: rename sweep", "research-synthesis"),
    ("standard-engineering", "CFP calendar (no prefix)", "standard-engineering"),
    ("rca-investigation", "AUDIT: why does X bypass Y", "rca-investigation"),
    ("novel-design", "RESEARCH: memory design", "novel-design"),
])
def test_assign_research_prefix_overrides_stale_stored_archetype(m, monkeypatch, stored,
                                                                 title, want):
    monkeypatch.setattr(router, "wq_show", lambda tid: dict(WQ4183, archetype=stored,
                                                            title=title))
    assert router.assign(4183, manifest=m, dry_run=True)["archetype"] == want


def test_explicit_archetype_still_beats_research_prefix(m, monkeypatch):
    monkeypatch.setattr(router, "wq_show", lambda tid: dict(WQ4183))
    d = router.assign(4183, archetype="standard-engineering", manifest=m, dry_run=True)
    assert d["archetype"] == "standard-engineering"


@pytest.mark.parametrize("detail", [
    WQ4628[1],                                                         # Research: label
    "Questions: deployment model, how to wire it in, overhead.",
    "Is it self-hosted? Does it wire into fworker? Cost? Overhead? Postgres?",
])
def test_unprefixed_research_brief_is_not_a_mechanical_edit(m, detail):
    arch, _ = router.classify_archetype("Trigger.dev durable execution", detail, "fleet", m)
    assert arch == "research-synthesis"


def test_brief_detector_ignores_few_questions_and_long_detail(m):
    detail = "wire the new flag through. does it need a restart? " + "log line\n" * 400
    assert router._research_brief_signals(detail) == []
    assert router.classify_archetype("wire the flag", detail, "fleet", m)[0] == "mechanical-edit"


@pytest.mark.parametrize("level", ["ok", "tight"])
@pytest.mark.parametrize("fails", [0, 1, 2])
def test_research_synthesis_never_selects_below_medium(m, level, fails):
    arch, _ = router.classify_archetype(*WQ4628, "build", m)
    rung, _ = router.select(m, arch, pressure=lambda x: level, fails=fails)
    assert rung is not None
    assert router.EFFORT_LADDER.index(rung.effort) >= EFFORT_FLOOR


# ---- wq#779 F3: cost-aware rubric resolution + attempt-count downgrade trigger ---------

def test_classify_cost_aware_tiebreak_on_equal_evidence(m):
    # "bypass" (rca-investigation, 1 non-weak hit, qualifies outright) vs "probe"
    # (probe-build, 1 hit): equally evidenced, no lane bias. Old list-order-first-wins
    # always picked rca-investigation (earlier in _RUBRIC) regardless of cost; cost-aware
    # resolution picks probe-build (sonnet-5@medium, the cheaper primary rung) on a tie
    # between it and a pricier archetype instead.
    arch, sig = router.classify_archetype("the fix could bypass the probe safeguard", "", "build", m)
    assert arch == "probe-build"
    assert sig


def test_classify_still_prefers_corroborated_expensive_archetype(m):
    # NOT pure cheapest-wins (the audit explicitly warns against that): a well-evidenced
    # rca-investigation (4 matched signals) still beats an incidental single probe-build
    # word in the same text.
    arch, sig = router.classify_archetype(
        "root cause audit: why does it flap, investigate the escape; a quick probe too",
        "", "build", m)
    assert arch == "rca-investigation"
    assert len(sig) >= 4


def test_attempt_count_downgrades_probe_build_without_pressure(m):
    # wq#2583: local-worker dropped from probe-build; primary is now sonnet-5@medium, and
    # each recorded fail walks one rung toward the haiku floor.
    r0, _ = router.select(m, "probe-build", fails=0)
    assert (r0.model, r0.effort) == ("claude-sonnet-5-5", "medium")
    r1, reasons = router.select(m, "probe-build", fails=1)
    assert r1.model == "claude-haiku-5-5"
    assert any("attempt-count downgrade" in w for w in reasons)
    r2, _ = router.select(m, "probe-build", fails=2)
    assert r2.model == "claude-haiku-5-5"


def test_attempt_count_downgrades_mechanical_edit_without_pressure(m):
    # wq#2583: mechanical-edit's base chain is now a single cloud rung (local-worker
    # dropped), so it never has a second position to walk to — fails just stays put.
    r1, _ = router.select(m, "mechanical-edit", fails=1)
    assert r1.model == "claude-sonnet-5-5" and r1.effort == "low" and r1.pool == "cloud"
    r2, _ = router.select(m, "mechanical-edit", fails=2)
    assert (r2.model, r2.effort) == ("claude-sonnet-5-5", "low")


def test_attempt_count_does_not_override_escalation_facts(m):
    # wq#5083: standard-engineering has no escalation rung any more (opus-5-5@high then
    # opus-4-8@high): fails=1 walks the base chain. rca-investigation's annotated fable
    # rung still unlocks on its own 2-fails condition, ahead of the base walk.
    r, _ = router.select(m, "standard-engineering", fails=1)
    assert (r.model, r.effort) == ("claude-opus-4-8", "high")
    r, _ = router.select(m, "rca-investigation", fails=2)
    assert (r.model, r.effort) == ("claude-fable-5-2", "high")


def test_route_cli_probe_build_fails_1_walks_to_the_haiku_floor(m):
    # `route probe-build --fails 1` with no --pressure (wq#2583: local-worker is gone, the
    # base chain is sonnet-5@medium then haiku-5-5 (wq#3618), so fails=1 already hits the floor).
    out = subprocess.run(
        [sys.executable, os.path.join(ORCH_DIR, "router.py"), "--manifest", MANIFEST,
         "route", "probe-build", "--fails", "1"],
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    d = json.loads(out.stdout)
    assert d["rung"] == {"model": "claude-haiku-5-5", "effort": None, "pool": "cloud",
                         "condition": None}


def test_campaign_decomposes_never_one_worker(m):
    rung, reasons = router.select(m, "long-horizon-campaign")
    assert rung.model == "DECOMPOSE"


# ---- dominated guard --------------------------------------------------------------------

def test_dominated_table_parsed(m):
    assert ("claude-fable-5", "low") in m.dominated_exact
    assert ("claude-fable-5", "medium") in m.dominated_exact
    assert ("claude-opus-5", "low") in m.dominated_exact
    assert m.dominated_scoped[("claude-sonnet-5", "xhigh")] == {"mechanical-edit", "probe-build"}
    assert m.dominated_scoped[("claude-sonnet-5", "max")] == {"mechanical-edit", "probe-build"}
    assert m.is_dominated("claude-sonnet-5", "xhigh", "probe-build")
    assert not m.is_dominated("claude-sonnet-5", "xhigh", "rca-investigation")
    # family rule: opus-4-7 / sonnet-4-6 while the 5-gen sibling is LIVE. wq#5083: opus-4-6
    # (orchestrator + security) and opus-4-8 (general second rung) are routed by Frank's call.
    for old in ("claude-opus-4-7", "claude-sonnet-4-6"):
        for e in router.EFFORT_LADDER:
            assert m.is_dominated(old, e), old
    for routed in ("claude-opus-4-8", "claude-opus-4-6"):
        assert not m.is_dominated(routed, "high"), routed


def test_dominated_never_selected_exhaustively(m):
    """Every archetype x every pressure combination x every escalation fact: the router
    never emits a dominated (model, effort)."""
    models = ["claude-fable-5", "claude-opus-5", "claude-sonnet-5", "claude-haiku-5-5"]
    levels = ["ok", "tight", "deny"]
    n = 0
    for arch in router.ARCHETYPES + ["orchestrator"]:
        for combo in itertools.product(levels, repeat=len(models)):
            table = dict(zip(models, combo))
            for fails, cross, contra in itertools.product((0, 1, 2), (False, True), (False, True)):
                rung, _ = router.select(m, arch, pressure=lambda mm: table.get(mm, "ok"),
                                        fails=fails, cross_subsystem=cross, contradiction=contra)
                n += 1
                if rung is None or rung.model == "DECOMPOSE":
                    continue
                assert not m.is_dominated(rung.model, rung.effort, arch), (arch, table, rung)
                # governor deny is honoured for cloud rungs
                if rung.pool == "cloud":
                    assert table.get(rung.model, "ok") != "deny", (arch, table, rung)
    assert n > 1000


def test_manifest_with_dominated_rung_in_chain_refuses_to_load(tmp_path):
    data = yaml.safe_load(open(MANIFEST))
    data["routing"]["mechanical-edit"] = ["fable-5@low", "sonnet-5@low"]
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(data))
    with pytest.raises(router.ManifestError, match="dominated"):
        router.load_manifest(str(p), force=True)


def test_family_dominance_lifts_if_sibling_not_live(tmp_path):
    data = yaml.safe_load(open(MANIFEST))
    for row in data["models"]:
        if row.get("id") == "claude-opus-5":
            row["status"] = "RETIRED"
    p = tmp_path / "no-opus5.yaml"
    p.write_text(yaml.safe_dump(data))
    mm = router.load_manifest(str(p), force=True)
    assert not mm.is_dominated("claude-opus-4-8", "high")
    assert mm.is_dominated("claude-sonnet-4-6", "high")


# ---- soft pressure does not move the tuple (wq#1723) ------------------------------------

def test_soft_pressure_leaves_the_tuple_alone(m):
    """wq#1723 deleted the effort-first downgrade. 'tight' must now select exactly the
    tuple 'ok' would, on every archetype — the burn-rate ceiling
    (dispatcher/burn_rate.py) is the whole budget response.
    Evidence: reports/wq1723_effort_first_downgrade.md."""
    for arch in router.ARCHETYPES:
        ok, _ = router.select(m, arch, pressure=lambda x: "ok")
        tight, why = router.select(m, arch, pressure=lambda x: "tight")
        if ok is None:
            assert tight is None, arch
            continue
        assert (tight.model, tight.effort) == (ok.model, ok.effort), arch
        assert not any("effort-first" in w for w in why), arch
    # the pressure reading is still recorded on the cloud rung it applied to
    _, why = router.select(m, "standard-engineering", pressure=lambda x: "tight")
    assert any("soft pressure noted" in w for w in why)


def test_effort_down_helper_is_gone(m):
    # wq#1723: the helper existed only to serve the deleted downgrade branch. If a future
    # change reintroduces it, that change owes fresh evidence against reports/wq1723/.
    assert not hasattr(router, "effort_down")


def test_hard_deny_moves_to_next_model_not_lower_effort(m):
    # wq#2583: mechanical-edit is now cloud-only (local-worker dropped, NARROW verdict), so
    # a sonnet deny has nowhere left to walk to.
    r, why = router.select(m, "mechanical-edit", pressure=lambda x: "deny" if "sonnet" in x else "ok")
    assert r is None
    assert why[-1] == "every rung denied, dominated or unavailable"
    # probe-build still has the haiku floor below sonnet: a sonnet deny lands there
    r, _ = router.select(m, "probe-build", pressure=lambda x: "deny" if "sonnet" in x else "ok")
    assert r.model == "claude-haiku-5-5" and r.effort is None
    r, why = router.select(m, "novel-design", pressure=lambda x: "deny")
    assert r is None


def test_escalation_rungs_need_their_facts(m):
    r, _ = router.select(m, "standard-engineering", fails=0)
    assert r.model == "claude-opus-5-5"
    r, _ = router.select(m, "standard-engineering", fails=1)
    assert (r.model, r.effort) == ("claude-opus-4-8", "high")
    r, _ = router.select(m, "rca-investigation", fails=1)
    assert r.model == "claude-opus-5-5"
    r, _ = router.select(m, "rca-investigation", fails=2)
    assert (r.model, r.effort) == ("claude-fable-5-2", "high")
    r, _ = router.select(m, "rca-investigation", cross_subsystem=True)
    assert r.model == "claude-fable-5-2"
    r, _ = router.select(m, "verify-pass", contradiction=True)
    assert (r.model, r.effort) == ("claude-opus-5-5", "high")


def test_effort_ceiling_clamp(m):
    assert m.clamp_effort("claude-haiku-5-5", "high") is None
    assert m.clamp_effort("claude-haiku-4-5", "high") is None
    assert m.clamp_effort("claude-opus-4-6", "xhigh") == "high"
    assert m.clamp_effort("claude-sonnet-5", "max") == "max"


# ---- refusal awareness ------------------------------------------------------------------

def test_refusal_landing_table_and_keep_entitled(m):
    assert m.refusal_landing("claude-fable-5", "bio") == "claude-opus-5"
    assert m.refusal_landing("claude-fable-5", "cyber") == "claude-opus-4-8"
    assert m.refusal_landing("claude-opus-5", "cyber") == "claude-opus-4-8"
    assert m.refusal_landing("claude-opus-5", "bio") is None
    assert "claude-opus-4-8" in m.keep_entitled()
    roster = {r["id"]: r for r in m.roster()}
    # wq#5083: opus-4-8 is routed now (standard-engineering / fire-response second rung)
    assert roster["claude-opus-4-8"]["keep_entitled"] and roster["claude-opus-4-8"]["routed"]
    assert str(roster["claude-opus-4-8"]["status"]).startswith("LIVE")


def test_classifier_records_refusal_fallback_frames():
    sys.path.insert(0, os.path.join(ORCH_DIR, "..", "harness"))
    import classifier
    st = classifier.RunState()
    st.observe({"type": "system", "subtype": "init", "session_id": "s", "model": "claude-fable-5"})
    st.observe({"type": "model_refusal_fallback", "original_model": "claude-fable-5",
                "fallback_model": "claude-opus-4-8", "api_refusal_category": "cyber"})
    st.observe({"type": "api_retry", "attempt": 1})
    st.observe({"type": "model_fallback", "reason": "overloaded", "fallback_model": "claude-opus-5"})
    st.observe({"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}})
    st.observe({"type": "result", "subtype": "success", "is_error": False})
    assert st.refusal_fallbacks == [{"original_model": "claude-fable-5",
                                     "fallback_model": "claude-opus-4-8", "category": "cyber"}]
    assert st.model_fallbacks[0]["reason"] == "overloaded"
    assert st.api_retries == 1
    assert classifier.classify(st, 0) == classifier.SUCCESS
    st.observe({"type": "model_refusal_no_fallback"})
    assert classifier.classify(st, 0) == classifier.REFUSED


# ---- verify-pass + judgment policy ------------------------------------------------------

def test_verify_pass_policy(m):
    assert judgment.verify_required(m, "standard-engineering")[0]
    assert judgment.verify_required(m, "rca-investigation")[0]
    assert not judgment.verify_required(m, "verify-pass")[0]
    assert not judgment.verify_required(m, "novel-design")[0]
    assert not judgment.verify_required(m, "mechanical-edit", "claude-sonnet-5")[0]
    assert judgment.verify_required(m, "mechanical-edit", "deepseek-v4-flash")[0]
    rung, note = judgment.verifier_tuple(m, "claude-opus-5")
    assert (rung.model, rung.effort) == ("claude-sonnet-5-5", "high")
    rung, note = judgment.verifier_tuple(m, "claude-sonnet-5-5")
    assert (rung.model, rung.effort) == ("claude-sonnet-5-5", "high") and "fresh instance" in note
    # flip prefer_distinct_model -> opus-5-5@high when the author was the verifier model
    m2 = copy.copy(m)
    m2.verify_policy = dict(m.verify_policy, prefer_distinct_model=True)
    rung, note = judgment.verifier_tuple(m2, "claude-sonnet-5-5")
    assert (rung.model, rung.effort) == ("claude-opus-5-5", "high")


def test_judgment_due_cadence_triggers_and_max_reserved(m):
    now = dt.datetime(2026, 8, 16, 3, 0, tzinfo=dt.timezone.utc)   # Sat 20:00 PT: not quiet hours
    empty = {"last_run_at": None, "last_run_tickets_done": None, "runs": []}
    d = judgment.due(m, now=now, state=empty)
    assert d["due"] and d["tuple"] == "claude-opus-4-6@high"   # wq#5083: orchestrator -> Opus 4.6
    recent = {"last_run_at": (now - dt.timedelta(minutes=30)).isoformat(), "last_run_tickets_done": 10, "runs": []}
    d = judgment.due(m, now=now, state=recent, triggers={"ticket_blocked_twice": [5]})
    assert not d["due"]  # min gap
    older = {"last_run_at": (now - dt.timedelta(hours=2)).isoformat(), "last_run_tickets_done": 10, "runs": []}
    d = judgment.due(m, now=now, state=older, tickets_done=12)
    assert not d["due"]
    d = judgment.due(m, now=now, state=older, tickets_done=18)
    assert d["due"] and "tickets closed" in " ".join(d["why"])
    d = judgment.due(m, now=now, state=older, triggers={"ticket_blocked_twice": [5]})
    assert d["due"] and d["tuple"] == "claude-opus-4-6@high"
    # hardest-investigation trigger: @max is reserved until CAL-1 measured data exists
    d = judgment.due(m, now=now, state=older, triggers={"rca_cross_subsystem": [7]})
    assert d["tuple"] == "claude-opus-4-6@high" and any("reserved until CAL-1" in w for w in d["why"])
    m2 = copy.copy(m)
    m2.raw = dict(m.raw, measured=[{"row": "x"}])
    d = judgment.due(m2, now=now, state=older, triggers={"rca_cross_subsystem": [7]})
    assert d["tuple"] == "claude-opus-4-6@max"
    # cadence inside weekday quiet hours waits; a burn-down trigger does not
    quiet = dt.datetime(2026, 8, 17, 15, 0, tzinfo=dt.timezone.utc)   # Mon 08:00 PT
    stale = {"last_run_at": (quiet - dt.timedelta(hours=8)).isoformat(), "last_run_tickets_done": 0, "runs": []}
    assert not judgment.due(m, now=quiet, state=stale, tickets_done=0)["due"]
    assert judgment.due(m, now=quiet, state=stale, tickets_done=0, triggers={"governor_floor_breach": ["x"]})["due"]


def test_governor_pressure_adapter(tmp_path):
    gov = tmp_path / "gov.py"
    gov.write_text("import sys; sys.exit(0 if 'deny' not in sys.argv[-1] else 1)\n")
    (tmp_path / "floors.json").write_text(json.dumps({
        "session_min_remaining_pct": 20, "weekly_all_min_remaining_pct": 15,
        "fable_weekly_min_remaining_pct": 10}))
    st = tmp_path / "state.json"
    st.write_text(json.dumps({"limits": {"session": {"percent": 7}, "weekly_all": {"percent": 30},
                                          "fable_weekly_scoped": {"percent": 82, "severity": "warning"}}}))
    assert router.governor_pressure("claude-sonnet-5", state_path=str(st), governor_cli=str(gov)) == "ok"
    assert router.governor_pressure("claude-fable-5", state_path=str(st), governor_cli=str(gov)) == "tight"
    assert router.governor_pressure("claude-deny", state_path=str(st), governor_cli=str(gov)) == "deny"
    st.write_text(json.dumps({"limits": {"session": {"percent": 70}, "weekly_all": {"percent": 30}}}))
    assert router.governor_pressure("claude-sonnet-5", state_path=str(st), governor_cli=str(gov)) == "tight"


def test_governor_pressure_local_pool_ignores_anthropic_pools(tmp_path):
    """wq#2081: a local-pool model draws zero Anthropic quota — exhausted session/weekly
    pools that deny/tighten a cloud model must not touch it (STOP still does, via
    can-dispatch)."""
    gov = tmp_path / "gov.py"
    gov.write_text("import sys; sys.exit(0 if 'deny' not in sys.argv[-1] else 1)\n")
    (tmp_path / "floors.json").write_text(json.dumps({
        "session_min_remaining_pct": 20, "weekly_all_min_remaining_pct": 15}))
    st = tmp_path / "state.json"
    st.write_text(json.dumps({"limits": {"session": {"percent": 99, "severity": "critical"},
                                          "weekly_all": {"percent": 99}}}))
    assert router.governor_pressure("claude-sonnet-5", state_path=str(st), governor_cli=str(gov)) == "deny"
    for m in ("qwen36-local", "nephthys-qwen14b", "hathor-llama8b"):
        assert router.governor_pressure(m, state_path=str(st), governor_cli=str(gov)) == "ok"
    assert router.governor_pressure("qwen36-local-deny", state_path=str(st), governor_cli=str(gov)) == "deny"


def test_governor_pressure_zeroed_floors_are_advisory(tmp_path):
    """wq#1669 (walkthrough sec 5, 2026-09-10): floors + soft margin zeroed, session
    80% / weekly 83% / fable 86% all severity=warning — the old code returned 'tight'
    on every pool (hardcoded SOFT_MARGIN_PCT + unconditional severity check) and
    clamped the fleet exactly during a burn-before-reset window. With floors.json
    zeroed (including soft_margin_pct), severity is advisory and the verdict is 'ok'."""
    gov = tmp_path / "gov.py"
    gov.write_text("import sys; sys.exit(0)\n")
    floors = tmp_path / "floors.json"
    floors.write_text(json.dumps({
        "session_min_remaining_pct": 0, "weekly_all_min_remaining_pct": 0,
        "fable_weekly_min_remaining_pct": 0, "soft_margin_pct": 0}))
    st = tmp_path / "state.json"
    st.write_text(json.dumps({"limits": {
        "session": {"percent": 80, "severity": "warning"},
        "weekly_all": {"percent": 83, "severity": "warning"},
        "fable_weekly_scoped": {"percent": 86, "severity": "warning"}}}))
    assert router.governor_pressure("claude-sonnet-5", state_path=str(st), governor_cli=str(gov)) == "ok"
    assert router.governor_pressure("claude-fable-5", state_path=str(st), governor_cli=str(gov)) == "ok"
    # wq#1699: warning != critical — critical engages tight on a zero-floor pool
    st.write_text(json.dumps({"limits": {
        "session": {"percent": 80, "severity": "warning"},
        "weekly_all": {"percent": 83, "severity": "warning"},
        "fable_weekly_scoped": {"percent": 94, "severity": "critical"}}}))
    assert router.governor_pressure("claude-sonnet-5", state_path=str(st), governor_cli=str(gov)) == "ok"
    assert router.governor_pressure("claude-fable-5", state_path=str(st), governor_cli=str(gov)) == "tight"
    st.write_text(json.dumps({"limits": {
        "session": {"percent": 80, "severity": "warning"},
        "weekly_all": {"percent": 95, "severity": "critical"},
        "fable_weekly_scoped": {"percent": 86, "severity": "warning"}}}))
    assert router.governor_pressure("claude-sonnet-5", state_path=str(st), governor_cli=str(gov)) == "tight"
    st.write_text(json.dumps({"limits": {
        "session": {"percent": 80, "severity": "warning"},
        "weekly_all": {"percent": 83, "severity": "warning"},
        "fable_weekly_scoped": {"percent": 86, "severity": "warning"}}}))
    # a nonzero floor re-arms the severity flag for that pool
    floors.write_text(json.dumps({
        "session_min_remaining_pct": 20, "weekly_all_min_remaining_pct": 0,
        "fable_weekly_min_remaining_pct": 0, "soft_margin_pct": 0}))
    assert router.governor_pressure("claude-sonnet-5", state_path=str(st), governor_cli=str(gov)) == "tight"
    # soft_margin_pct in floors.json overrides the hardcoded default even with floors 0
    floors.write_text(json.dumps({
        "session_min_remaining_pct": 0, "weekly_all_min_remaining_pct": 0,
        "fable_weekly_min_remaining_pct": 0, "soft_margin_pct": 30}))
    st.write_text(json.dumps({"limits": {
        "session": {"percent": 80}, "weekly_all": {"percent": 10}}}))
    assert router.governor_pressure("claude-sonnet-5", state_path=str(st), governor_cli=str(gov)) == "tight"


def test_dispatch_cmd_carries_systemd_resource_limits(monkeypatch):
    # RELIABILITY_SPEC.md §E / wq#648 (S3): every fworker-wq<ID> unit gets MemoryMax +
    # MemoryHigh + CPUQuota + TasksMax + OOMPolicy=stop, not just TimeoutStopSec.
    monkeypatch.setattr(router, "wq_show", lambda tid: {
        "id": str(tid), "model": "claude-sonnet-5", "effort": "high", "archetype": "standard-engineering",
    })
    cmd = router.dispatch_cmd(648, "/home/lumen/foreman")
    assert cmd[:3] == ["lumen-run", "start", "--name"]
    assert "--mem" in cmd and cmd[cmd.index("--mem") + 1] == router.WORKER_MEM_MAX
    assert "--mem-high" in cmd and cmd[cmd.index("--mem-high") + 1] == router.WORKER_MEM_HIGH
    assert "--cpu" in cmd and cmd[cmd.index("--cpu") + 1] == router.WORKER_CPU_QUOTA
    assert "--tasks-max" in cmd and cmd[cmd.index("--tasks-max") + 1] == router.WORKER_TASKS_MAX
    assert "--oom-policy" in cmd and cmd[cmd.index("--oom-policy") + 1] == "stop"
    assert "--timeout-stop-sec" in cmd and cmd[cmd.index("--timeout-stop-sec") + 1] == "90"
    assert cmd.index("--") < cmd.index("--model")  # resource flags precede the -- separator


def test_dispatch_cmd_wraps_fire_response_session_in_external_timeout(monkeypatch):
    # wq#1261 (#13 item 2): the fire-response session gets an external `timeout` wrap at
    # the dispatch path itself — belt-and-braces for a wedged fworker that never reaches
    # fire_gate.runtime_exceeded()'s in-process check.
    monkeypatch.setattr(router, "wq_show", lambda tid: {
        "id": str(tid), "model": "claude-sonnet-5", "effort": "high", "archetype": "fire-response",
    })
    cmd = router.dispatch_cmd(717, "/home/lumen/foreman")
    dash = cmd.index("--")
    tail = cmd[dash + 1:]
    assert tail[:4] == ["timeout", "--signal=TERM",
                        f"--kill-after={router.FIRE_MAX_RUNTIME_KILL_AFTER_S}",
                        str(router.FIRE_MAX_RUNTIME_S)]
    assert tail[4] == "python3"
    assert "--archetype" in cmd and cmd[cmd.index("--archetype") + 1] == "fire-response"


def test_dispatch_cmd_does_not_wrap_non_fire_archetypes(monkeypatch):
    monkeypatch.setattr(router, "wq_show", lambda tid: {
        "id": str(tid), "model": "claude-sonnet-5", "effort": "high", "archetype": "standard-engineering",
    })
    cmd = router.dispatch_cmd(717, "/home/lumen/foreman")
    dash = cmd.index("--")
    assert cmd[dash + 1] == "python3"  # no timeout wrap for a non-fire archetype


# ---- wq#713: lane -> repo, dispatch-cmd must never default to /home/lumen --------------

def test_dispatch_cmd_derives_repo_from_mapped_lane(monkeypatch):
    monkeypatch.setattr(router, "wq_show", lambda tid: {
        "id": str(tid), "lane": "pokemon", "model": "claude-sonnet-5", "effort": "high",
        "archetype": "standard-engineering",
    })
    cmd = router.dispatch_cmd(713)
    assert "--repo" in cmd and cmd[cmd.index("--repo") + 1] == "/home/lumen/pokemon"


def test_dispatch_cmd_explicit_repo_overrides_lane(monkeypatch):
    monkeypatch.setattr(router, "wq_show", lambda tid: {
        "id": str(tid), "lane": "pokemon", "model": "claude-sonnet-5", "effort": "high",
        "archetype": "standard-engineering",
    })
    cmd = router.dispatch_cmd(713, "/tmp/some-other-tree")
    assert cmd[cmd.index("--repo") + 1] == "/tmp/some-other-tree"


def test_dispatch_cmd_unmapped_lane_without_repo_raises_never_defaults_to_home_lumen(monkeypatch):
    monkeypatch.setattr(router, "wq_show", lambda tid: {
        "id": str(tid), "lane": "github", "model": "claude-sonnet-5", "effort": "high",
        "archetype": "standard-engineering",
    })
    with pytest.raises(SystemExit, match="no LANE_REPO entry"):
        router.dispatch_cmd(713)


def test_lane_repo_map_covers_build_with_foreman_root():
    assert router.repo_for_lane("build") == router.FOREMAN_ROOT
    assert router.repo_for_lane("nonexistent-lane") is None


# wq#1325/ISSUE-LOG #125: LANE_REPO["nest"] once pointed at /home/lumen/projects/nest, an
# archive/scratch repo of unrelated RE notes — not the git-tracked firmware repo, which
# lives one level deeper (its own history/remote, .gitignore'd out of the archive per
# bd5c4f1). No test pinned the exact value, so it regressed silently once already
# (wq#1155). Pin it so a future edit can't quietly point it back at the archive dir.
def test_lane_repo_nest_points_at_firmware_repo_not_archive_dir():
    assert router.repo_for_lane("nest") == "/home/lumen/projects/nest/NoLongerEvil-Thermostat"
    assert router.repo_for_lane("nest") != "/home/lumen/projects/nest"


# wq#830: 'auto' is wq's CLI default lane and spans arbitrary repos (wq#688 questfree,
# wq#306 vault, wq#545 wq-tooling), so it must not resolve to FOREMAN_ROOT. Dispatching an
# auto ticket without --repo has to fail loudly, not hand fworker the wrong tree.
def test_auto_lane_defaults_to_foreman():
    # 1eb5c48 (wq#2103): auto-lane infra lands in foreman, like fleet/ops — but only
    # as the LAST resort; every per-ticket hint outranks it (wq#2753).
    assert router.repo_for_lane("auto") == router.FOREMAN_ROOT


def test_dispatch_cmd_unmapped_lane_without_repo_raises(monkeypatch):
    monkeypatch.setattr(router, "wq_show", lambda tid: {
        "id": str(tid), "lane": "no-such-lane", "model": "claude-sonnet-5",
        "effort": "high", "archetype": "standard-engineering",
    })
    with pytest.raises(SystemExit, match="no LANE_REPO entry"):
        router.dispatch_cmd(688)
    cmd = router.dispatch_cmd(688, "/home/lumen/questfree")
    assert cmd[cmd.index("--repo") + 1] == "/home/lumen/questfree"


# ---- wq#1123: dispatch-cmd (the manual CLI, per its own docstring) must resolve a
# repo-sweep ticket's repo hint the same way foreman_dispatcher's automated pass does
# (wq#1094) — via the shared router.repo_for_ticket(), not just LANE_REPO. ----------------

def test_dispatch_cmd_auto_lane_repo_sweep_ticket_resolves_via_repo_hint(monkeypatch):
    repo = "/home/lumen/questfree"
    monkeypatch.setattr(repo_sweep, "managed_repos", lambda: {repo})
    monkeypatch.setattr(router, "wq_show", lambda tid: {
        "id": str(tid), "lane": "auto", "model": "claude-sonnet-5", "effort": "high",
        "archetype": "standard-engineering",
        "title": "[repo-sweep] questfree: BEHIND_ONLY",
        "detail": f"Automated sweep detected BEHIND_ONLY in {repo}.\n"
                  f"Output: BEHIND_ONLY {repo} main ahead=0 behind=1",
    })
    cmd = router.dispatch_cmd(1089)  # no --repo, like a human running the CLI directly
    assert cmd[cmd.index("--repo") + 1] == repo


def test_repo_for_ticket_prefers_lane_repo_over_hint():
    assert router.repo_for_ticket({"lane": "pokemon", "title": "", "detail": ""}) == "/home/lumen/pokemon"


def test_repo_for_ticket_falls_back_to_repo_hint(monkeypatch):
    repo = "/home/lumen/vault"
    monkeypatch.setattr(repo_sweep, "managed_repos", lambda: {repo})
    t = {"lane": "auto", "title": "[repo-sweep] vault: BEHIND_ONLY",
         "detail": f"Automated sweep detected BEHIND_ONLY in {repo}.\nOutput: x"}
    assert router.repo_for_ticket(t) == repo


def test_repo_for_ticket_none_when_neither_resolves():
    # lane 'auto' resolves foreman since 1eb5c48 (wq#2103); a lane outside LANE_REPO
    # is the remaining no-hint shape and must still refuse rather than guess.
    assert router.repo_for_ticket({"lane": "no-such-lane", "title": "plain ticket",
                                   "detail": ""}) is None


# ---- wq#1496: the tests above all monkeypatch router.wq_show() to return a dict with a
# real Python '\n' already in it — that hides the actual bug entirely, since the bug is
# in how wq_show() itself turns the real `wq show` CLI's plain-text output (which
# escapes embedded newlines as the literal 2-char sequence backslash-n, wq#1277) into a
# dict. These drive the real /home/lumen/bin/wq binary against a throwaway DB so the
# parsing itself is under test, not just its callers. ------------------------------------

def test_wq_show_returns_real_newlines_not_escaped_text(tmp_path, monkeypatch):
    monkeypatch.setenv("WQ_DB", str(tmp_path / "queue.db"))
    monkeypatch.delenv("WQ_AGENT", raising=False)
    detail = "line one\n\nVerify repo: /home/lumen/pokemon\nline four"
    p = router.wq("add", "--lane", "build", "--title", "scratch wq1496 multiline", "--detail", detail)
    assert p.returncode == 0, p.stdout + p.stderr
    tid = int(re.search(r"added task (\d+)", p.stdout).group(1))
    t = router.wq_show(tid)
    assert t["detail"] == detail, (
        f"wq_show() mangled the multi-line detail — got {t['detail']!r}, "
        f"expected a real newline before 'Verify repo:', not a literal backslash-n")


def test_wq_show_missing_ticket_still_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("WQ_DB", str(tmp_path / "queue.db"))
    monkeypatch.delenv("WQ_AGENT", raising=False)
    router.wq("add", "--title", "make sure the db exists")  # cmd_show --json on an empty/missing DB
    with pytest.raises(SystemExit):
        router.wq_show(999999)


def test_verify_repo_hint_resolves_through_the_real_wq_show_round_trip(tmp_path, monkeypatch):
    """wq#1496's own reproduction: a real ticket, added via the real CLI, whose detail's
    'Verify repo:' line only a correctly-newlined wq_show() lets verify_repo_hint see."""
    monkeypatch.setenv("WQ_DB", str(tmp_path / "queue.db"))
    monkeypatch.delenv("WQ_AGENT", raising=False)
    monkeypatch.setattr(repo_sweep, "managed_repos", lambda: {"/home/lumen/pokemon"})
    detail = "Adversarial verify-pass of wq#100 — PR#1.\n\nVerify repo: /home/lumen/pokemon"
    p = router.wq("add", "--lane", "build", "--title", "[verify] wq#100 something shipped",
                  "--detail", detail)
    assert p.returncode == 0, p.stdout + p.stderr
    tid = int(re.search(r"added task (\d+)", p.stdout).group(1))
    assert router.repo_for_ticket(tid) == "/home/lumen/pokemon"


# ---- wq#1220: a verify-pass ticket is filed under the static verify_pass policy lane
# ('build'), not the shipped ticket's own lane — so LANE_REPO alone would point dispatch
# at claude-foreman instead of e.g. mailbot. judgment.file_verify_pass carries the
# shipped ticket's resolved repo back in its own detail; repo_for_ticket must read it
# back and prefer it over the verify ticket's 'build' lane. ------------------------------

def test_verify_repo_hint_overrides_verify_ticket_own_lane():
    t = {"lane": "build", "title": "[verify] wq#1173 add mailbot lane routing",
         "detail": "Adversarial verify-pass ... \n\nVerify repo: /home/lumen/mailbot"}
    assert router.repo_for_ticket(t) == "/home/lumen/mailbot"


def test_verify_repo_hint_ignored_when_not_a_verify_ticket():
    t = {"lane": "build", "title": "not a verify ticket",
         "detail": "Verify repo: /home/lumen/mailbot"}
    assert router.repo_for_ticket(t) == router.FOREMAN_ROOT


def test_verify_repo_hint_none_when_repo_not_managed():
    t = {"lane": "build", "title": "[verify] wq#1173 add mailbot lane routing",
         "detail": "Verify repo: /home/lumen/not-a-real-repo"}
    assert router.repo_for_ticket(t) == router.FOREMAN_ROOT


def test_file_verify_pass_embeds_shipped_ticket_repo_hint(monkeypatch, m):
    monkeypatch.setattr(router, "wq_show", lambda tid: {
        "id": str(tid), "lane": "mailbot", "title": "add mailbot lane routing",
        "detail": "", "notes": "", "archetype": "standard-engineering",
        "model": "claude-sonnet-5",
    })
    calls = []

    def fake_wq(*args):
        calls.append(args)
        out = subprocess.CompletedProcess(args, 0)
        out.stdout = "" if args[0] == "list" else "added task 9999"
        out.stderr = ""
        return out

    monkeypatch.setattr(router, "wq", fake_wq)
    out = judgment.file_verify_pass(1173, manifest=m)
    assert out["filed"] == 9999
    add_call = next(c for c in calls if c[0] == "add")
    detail = add_call[add_call.index("--detail") + 1]
    assert "Verify repo: /home/lumen/mailbot" in detail
    assert add_call[add_call.index("--lane") + 1] == "build"  # policy lane unchanged


def test_dispatch_cmd_verify_pass_ticket_resolves_shipped_repo_not_own_lane(monkeypatch):
    # wq#1220 repro: a mailbot-lane ticket's verify-pass is filed lane=build (policy
    # default) — dispatch_cmd must still point the worker at /home/lumen/mailbot, not
    # FOREMAN_ROOT, using the hint file_verify_pass wrote into the verify ticket's detail.
    monkeypatch.setattr(router, "wq_show", lambda tid: {
        "id": str(tid), "lane": "build", "model": "claude-sonnet-5", "effort": "high",
        "archetype": "verify-pass",
        "title": "[verify] wq#1173 add mailbot lane routing",
        "detail": "Adversarial verify-pass ...\n\nVerify repo: /home/lumen/mailbot",
    })
    cmd = router.dispatch_cmd(1217)  # no --repo, like the automated dispatcher
    assert cmd[cmd.index("--repo") + 1] == "/home/lumen/mailbot"


# ---- wq#778: classifier corroboration gate (from wq#750 audit F1/F2) --------------------
# research/MODEL_ROUTING_AUDIT.md found classify_archetype() sent 34.8%/18.4% of all 772
# filed tickets to research-synthesis/rca-investigation — both opus-primary rungs — with
# 66%/48% of that load resting on ONE bare word ('research'/'report'/'audit'/'why') that
# shows up in ordinary ticket prose. _WEAK_SIGNALS + _qualifies() require a second signal
# or a phrase-level pattern before those two archetypes can win.

QUEUE_DB = "/home/lumen/agents/work-queue/queue.db"


@pytest.mark.parametrize("title,disallowed", [
    ("just some research into vendor pricing", "research-synthesis"),
    ("weekly status report for the fleet", "research-synthesis"),
    ("audit the deploy checklist", "rca-investigation"),
    ("why did the build take longer today", "rca-investigation"),
])
def test_bare_word_signal_alone_never_wins_its_opus_primary_archetype(title, disallowed):
    arch, sig = router.classify_archetype(title, "", "")
    assert arch != disallowed, (title, arch, sig)


def test_second_signal_or_phrase_level_pattern_still_wins():
    # two weak signals together corroborate each other
    arch, _ = router.classify_archetype("research pack: agent farm prior art report", "", "")
    assert arch == "research-synthesis"
    # a weak signal plus an unrelated strong hit from the same archetype corroborates too
    arch, _ = router.classify_archetype("audit: why does lpp-watchdog bypass the guard", "", "")
    assert arch == "rca-investigation"
    # a phrase-level pattern wins alone, same as before wq#778
    arch, _ = router.classify_archetype("root cause of the flaky deploy", "", "")
    assert arch == "rca-investigation"


def test_lane_bias_cannot_resurrect_a_disqualified_archetype():
    # lane "research" biases toward research-synthesis, but a bare 'research' hit alone
    # must still not win it just because the lane says so (wq#778 regression: the original
    # fix let the no-signal fallback path re-apply lane bias and undo the gate).
    arch, sig = router.classify_archetype("research into vendor pricing", "", "research")
    assert arch != "research-synthesis", (arch, sig)


def _replay_queue_db():
    if not os.path.exists(QUEUE_DB):
        pytest.skip(f"queue.db not present at {QUEUE_DB} — not running on the fleet host")
    with sqlite3.connect(f"file:{QUEUE_DB}?mode=ro", uri=True) as c:
        return list(c.execute("select lane, title, detail from tasks"))


def test_classifier_replay_keeps_opus_primary_archetypes_under_15_percent():
    """ACCEPTANCE (wq#778): replayed over every filed ticket, research-synthesis and
    rca-investigation must each land below 15%, without pushing the no-signal
    standard-engineering fallthrough above its pre-fix baseline (~27%)."""
    rows = _replay_queue_db()
    n = len(rows)
    dist = collections.Counter()
    no_signal = 0
    for lane, title, detail in rows:
        arch, sig = router.classify_archetype(title or "", detail or "", lane or "")
        dist[arch] += 1
        if not sig:
            no_signal += 1
    assert dist["research-synthesis"] / n < 0.15, dict(dist)
    assert dist["rca-investigation"] / n < 0.15, dict(dist)
    assert no_signal / n <= 0.27, no_signal


# ---- wq#1707: analysis tickets must never land on a mechanical-edit / DECOMPOSE rung ----
# Both cases below are real tickets Frank hand-corrected on 2026-09-10, recovered from
# queue.db by comparing each row's stored archetype against what classify_archetype()
# produced for the same text.

# wq#1669 verbatim: a governor scaling design. Its ONLY mechanical-edit evidence is the
# word "wire" inside "budget remaining vs time to reset ... rewire"-style prose; its only
# novel-design evidence is "design". Equal counts, so the wq#779 F3 cost tie-break handed
# it to mechanical-edit -> qwen36-local until this fix.
WQ1669_TITLE = ("governor tight-mode ignores zeroed floors; spawn rate should scale "
                "with budget remaining vs time to reset")
WQ1669_DETAIL = ("FOUND by wq#1667 walkthrough. Frank zeroed governor/floors.json and "
                 "pacing policy=off, but router.governor_pressure still returns tight. "
                 "Design the replacement pacing curve and wire it through.")

# wq#1628 verbatim-ish: a log post-mortem whose only long-horizon-campaign evidence is the
# word "checkpoint" (LPP save-state vocabulary, per wq#1143), which DECOMPOSEd it.
WQ1628_TITLE = "LPP full behavioral analysis from run logs — why it freezes"
WQ1628_DETAIL = ("Heavy log-analysis + design (no code changes). Do a full behavioral "
                 "post-mortem from the data: progress.json, events/*.jsonl, "
                 "stuck_escalation.json, and the resume/checkpoint logs.")


def test_wq1669_analysis_ticket_no_longer_routes_to_mechanical_edit(m):
    arch, sig = router.classify_archetype(WQ1669_TITLE, WQ1669_DETAIL, "build", m)
    assert arch != "mechanical-edit", (arch, sig)
    assert arch == "novel-design", (arch, sig)
    rung, _ = router.select(m, arch)
    assert rung.model != "qwen36-local"


def test_wq1628_checkpoint_alone_no_longer_decomposes(m):
    arch, sig = router.classify_archetype(WQ1628_TITLE, WQ1628_DETAIL, "build", m)
    assert arch != "long-horizon-campaign", (arch, sig)
    rung, _ = router.select(m, arch)
    assert rung.model != "DECOMPOSE"


def test_strong_campaign_signals_still_decompose_alone(m):
    # the wq#992 line holds: checkpoint joins \bchain\b as weak, the rest stay strong.
    for title in ("multi-session campaign to port the fleet off systemd timers",
                  "uplift every worker prompt to the new instincts",
                  "pivot the memory subsystem onto the minerva store"):
        arch, sig = router.classify_archetype(title, "", "", m)
        assert arch == "long-horizon-campaign", (title, arch, sig)


@pytest.mark.parametrize("title", [
    # each pairs a genuine analysis signal with an incidental mechanical-edit word
    "root cause of the duplicate dispatch; rename the counter while we are in there",
    "design doc for the new alert daemon — also bump the schema version",
    "research pack on local model pricing, report includes a one-liner fix",
])
def test_incidental_mechanical_word_never_beats_an_analysis_signal(m, title):
    arch, sig = router.classify_archetype(title, "", "fleet", m)
    assert arch != "mechanical-edit", (arch, sig)


def test_fleet_lane_bias_cannot_pick_mechanical_edit_over_analysis(m):
    # _LANE_BIAS['fleet'] lists mechanical-edit first, which is the second path (besides
    # the cost tie-break) that could put an analysis ticket on qwen36-local.
    arch, sig = router.classify_archetype(
        "root cause of the watchdog flap; then rename the stale unit", "", "fleet", m)
    assert arch == "rca-investigation", (arch, sig)


def test_pilot_lane_bias_cannot_pick_probe_build_over_analysis(m):
    # wq#1755: _LANE_BIAS['pilot'] lists probe-build first, which could put a corroborated
    # analysis ticket (here: novel-design's "design" signal) on sonnet-5 instead of
    # fable-5/opus-5, the same failure class as the fleet/mechanical-edit case above.
    arch, sig = router.classify_archetype(
        "design doc for pilot benchmark harness", "", "pilot", m)
    assert arch != "probe-build", (arch, sig)


def test_mechanical_edit_still_wins_when_nothing_analytical_is_evidenced(m):
    # the guard is scoped: a true mechanical edit is untouched and still reaches its
    # primary rung (this is the SAMPLES row at the top of the file; wq#2583 moved that
    # rung to sonnet-5@low, off local-worker).
    arch, sig = router.classify_archetype("wire standing checks to systemd timers", "",
                                          "fleet", m)
    assert arch == "mechanical-edit", (arch, sig)
    rung, _ = router.select(m, arch)
    assert (rung.model, rung.effort) == ("claude-sonnet-5-5", "low")
    # and the wq#779 F3 tie-break still lets the OTHER cheap archetype win on equal
    # evidence against a pricier one — that fix is not what misrouted these tickets.
    arch, _ = router.classify_archetype("the fix could bypass the probe safeguard", "",
                                        "build", m)
    assert arch == "probe-build"


# ---- wq#1725: probe-build's bare-word signals must not fire from detail prose alone ----

def test_detail_only_probe_word_does_not_win_probe_build(m):
    # wq#1724 verbatim shape: a wq-tooling + audit ticket whose detail mentions "a named
    # live probe" was classified probe-build off that one word and routed to
    # deepseek-v4-flash. The title carries no probe-build vocabulary at all, so the
    # detail-only "probe" must not count.
    arch, sig = router.classify_archetype(
        "wq-tooling: reconcile the audit trail for lumen-pr merges",
        "root cause: a named live probe left a stale lockfile the last time this ran.",
        "build", m)
    assert arch != "probe-build", (arch, sig)


def test_title_probe_word_still_wins_probe_build(m):
    # same signal, but in the title — still fires alone, same as before wq#1725.
    arch, sig = router.classify_archetype("probe the CI hygiene job", "", "build", m)
    assert arch == "probe-build", (arch, sig)


# ---- wq#2615: a design-doc PATH citation is a reference, not a design deliverable ----

# wq#2607 verbatim (token id elided): one of the nine MCP-HA provisioning tickets whose
# detail opens by citing the HA design doc. Every one routed novel-design -> fable-5@high
# off that citation until this fix; the deliverable is provisioning, not a design.
WQ2607_TITLE = "DC endpoint on Anubis: dc-gateway + own cloudflared tunnel + Access app"
WQ2607_DETAIL = (
    "Design /home/lumen/instructions/drafts/MCP_CONTROL_PLANE_HA_DESIGN.md §3.2. Anubis "
    "(i5 4C/8T, 32G, 3w uptime, load 0.5; verified 2026-09-25 no node, no cloudflared). "
    "Steps: install node LTS; npm i -g supergateway@3.4.3 "
    "@wonderwhy-er/desktop-commander@0.2.46 (pin = Lumen's, verified); copy "
    "dc-enforce-config.py; unit dc-gateway.service (copy of Lumen desktop-commander-mcp "
    "incl. T1 drop-in hardening) on :3100 stateless; T2 watchdog + timer; cloudflared "
    "install, 'cloudflared tunnel create anubis-mcp' on ANUBIS (not Lumen — the 06-29 "
    "gotcha), ingress mcp-anubis.lumennode.uk -> http://localhost:3100, DNS CNAME "
    "<uuid>.cfargotunnel.com via DNS API using the ANUBIS uuid; CF Access self-hosted "
    "app mcp-anubis.lumennode.uk with policy = existing 'Worker service token' "
    "<token-id> (pattern of mcp-failover 06-29); prove 403 without token, 200 "
    "tools/list with token.")


def test_wq2607_design_doc_citation_does_not_win_novel_design(m):
    arch, sig = router.classify_archetype(WQ2607_TITLE, WQ2607_DETAIL, "fleet", m)
    assert arch != "novel-design", (arch, sig)


def test_design_doc_filename_or_path_is_not_a_design_signal(m):
    # same reference class, in a TITLE: filename / path-component mentions don't count.
    for title in ("update the provisioning steps to match DESIGN.md",
                  "sync the runbook with docs/design/overview.md"):
        arch, sig = router.classify_archetype(title, "", "", m)
        assert arch != "novel-design", (title, arch, sig)


def test_design_intent_still_wins_from_title_or_detail(m):
    # prose intent in the title, unchanged
    arch, _ = router.classify_archetype("design doc for the new alert daemon", "", "", m)
    assert arch == "novel-design"
    # detail-only prose intent — the wq#1669 line holds; this is why the fix is a
    # reference-shape guard on the pattern, not a title-only gate (wq#1725 style), which
    # would send this ticket back to mechanical-edit/qwen36-local.
    arch, sig = router.classify_archetype(
        "governor spawn rate is wrong",
        "Design the replacement pacing curve and document the interface decision.",
        "", m)
    assert arch == "novel-design", (arch, sig)


def test_replay_no_filed_ticket_with_analysis_evidence_is_a_mechanical_edit(m):
    """ACCEPTANCE (wq#1707): over every filed ticket, no row whose text carries CORROBORATED
    novel-design / rca-investigation / research-synthesis evidence may classify as
    mechanical-edit. Corroborated, not merely matched: wq#778 already settled that one bare
    word ('report', 'audit') is not evidence of anything, so such a row is a legitimate
    mechanical edit and the guard deliberately lets it through."""
    offenders = []
    checked = 0
    for lane, title, detail in _replay_queue_db():
        text = f"{title or ''}\n{detail or ''}"
        analysis = []
        for a, pats in router._RUBRIC:
            if a not in router._ANALYSIS_ARCHETYPES:
                continue
            matched = [p for p in pats if re.search(p, text, re.IGNORECASE)]
            if matched and router._qualifies(a, matched):
                analysis.append(a)
        if not analysis:
            continue
        checked += 1
        arch, _ = router.classify_archetype(title or "", detail or "", lane or "", m)
        if arch == "mechanical-edit":
            offenders.append((title, analysis))
    assert checked > 0, "no ticket carried analysis evidence — test would pass vacuously"
    assert not offenders, offenders


# ---- end-to-end against a scratch wq (needs a wq with the wq#617 columns) --------------

def _wq_supports_tuple(wq_bin):
    p = subprocess.run([wq_bin, "add", "-h"], capture_output=True, text=True)
    return "--model" in p.stdout


def test_assign_and_verify_pass_fire_on_scratch_wq(tmp_path):
    wq_bin = os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")
    if not _wq_supports_tuple(wq_bin):
        pytest.skip(f"{wq_bin} lacks wq#617 --model/--effort (bin PR not landed yet)")
    env = dict(os.environ, WQ_DB=str(tmp_path / "q.db"), WQ_AGENT="t617-pytest",
               FOREMAN_WQ_BIN=wq_bin, FOREMAN_MODELS_YAML=os.path.abspath(MANIFEST),
               FOREMAN_RUNS_ROOT=str(tmp_path / "runs"),
               FOREMAN_STATE_JSON=str(tmp_path / "state.json"),
               FOREMAN_JUDGMENT_STATE=str(tmp_path / "runs" / "judgment.json"))
    R = [sys.executable, os.path.join(ORCH_DIR, "router.py")]
    J = [sys.executable, os.path.join(ORCH_DIR, "judgment.py")]

    def wq(*a):
        return subprocess.run([wq_bin, *a], capture_output=True, text=True, env=env)

    ids = []
    for title, detail, lane, arch, expected in SAMPLES[:5]:
        # wq#912 refuses `add` on a lane absent from dispatcher/config.json (checked
        # globally, independent of WQ_DB). This test only exercises router/judgment
        # assign+verify-pass logic against a scratch db -- dispatcher visibility of
        # these throwaway tickets is out of scope -- so --force bypasses that gate.
        add = wq("add", "--lane", lane, "--title", title, "--force")
        assert add.returncode == 0, add.stdout + add.stderr
        tid = int(add.stdout.split()[2]); ids.append((tid, arch, expected))
    for tid, arch, expected in ids:
        d = json.loads(subprocess.run(R + ["assign", str(tid)], capture_output=True, text=True, env=env).stdout)
        assert d["written"], d
        assert d["archetype"] == arch
        assert (d["rung"]["model"], d["rung"]["effort"]) == expected
        show = wq("show", str(tid)).stdout
        assert f"model        : {expected[0]}" in show and f"effort       : {expected[1]}" in show
    # verify-pass fires on a shipped diff (standard-engineering ticket #3), not on novel-design
    se = [t for t, a, _ in ids if a == "standard-engineering"][0]
    nd = [t for t, a, _ in ids if a == "novel-design"][0]
    wq("claim", "--id", str(se)); wq("done", str(se), "--notes", "PR https://example/x/pull/1")
    d = json.loads(subprocess.run(J + ["verify-pass", "--for", str(se)], capture_output=True, text=True, env=env).stdout)
    assert d["required"] and d["filed"], d
    vt = wq("show", str(d["filed"])).stdout
    assert "archetype    : verify-pass" in vt and "model        : claude-sonnet-5" in vt \
        and "effort       : high" in vt and f"deps         : {se}" in vt
    d2 = json.loads(subprocess.run(J + ["verify-pass", "--for", str(se)], capture_output=True, text=True, env=env).stdout)
    assert d2["filed"] == d["filed"]          # idempotent
    d3 = json.loads(subprocess.run(J + ["verify-pass", "--for", str(nd)], capture_output=True, text=True, env=env).stdout)
    assert not d3["required"] and d3["filed"] is None
    # dispatch-cmd carries the tuple through to fworker (wq#2583: mechanical-edit's
    # primary is now sonnet-5@low, off local-worker)
    cmd = subprocess.run(R + ["dispatch-cmd", str(ids[0][0]), "--repo", "/tmp/x"], capture_output=True, text=True, env=env).stdout
    assert "--model claude-sonnet-5-5 --effort low --archetype mechanical-edit" in cmd


# ---- wq#433 / ISSUE-LOG #56 class: lane says one repo, title says another. Dispatching
# on the lane alone hands the worker a tree with none of the paths its ticket cites, and
# --add-dir only grants the right tree READ access, so the attempt cannot land anywhere.
# Refuse loudly instead of guessing. ----------------------------------------------------

def test_title_repo_marker_reads_bracketed_and_bare_leading_tags():
    assert router.title_repo_marker("[lpp-mp] M5d: cross-node link over Tailscale") == "/home/lumen/pokemon"
    assert router.title_repo_marker("[lpp][stuck] agent parked 40 turns") == "/home/lumen/pokemon"
    assert router.title_repo_marker("LPP-MP M5 (stretch): BGB link-over-TCP bridge") == "/home/lumen/pokemon"
    assert router.title_repo_marker("[nest] wire the porch sensor") == "/home/lumen/projects/nest/NoLongerEvil-Thermostat"
    assert router.title_repo_marker("[questfree] Quest 1 DTS") == "/home/lumen/questfree"


def test_title_repo_marker_none_for_ordinary_titles():
    assert router.title_repo_marker("fix(dispatcher): lint committed lanes") is None
    assert router.title_repo_marker("[repo-sweep] vault: BEHIND_ONLY") is None
    assert router.title_repo_marker("") is None
    assert router.title_repo_marker(None) is None


def test_repo_mismatch_flags_wq433_shape_and_names_both_repos():
    t = {"lane": "build", "title": "LPP-MP M5 (stretch): BGB link-over-TCP bridge", "detail": ""}
    reason = router.repo_mismatch(t)
    assert reason and "/home/lumen/pokemon" in reason and router.FOREMAN_ROOT in reason


def test_repo_mismatch_silent_when_lane_and_title_agree():
    assert router.repo_mismatch(
        {"lane": "pokemon", "title": "[lpp-mp] M5e: interop vs a real BGB build", "detail": ""}) is None
    assert router.repo_mismatch(
        {"lane": "build", "title": "fix(dispatcher): lint committed lanes", "detail": ""}) is None


def test_repo_mismatch_exempts_verify_pass_tickets():
    # wq#1220: a verify ticket's lane is a static policy default and its real repo lives
    # in its own detail — the marker check must not fight that resolver.
    t = {"lane": "build", "title": "[verify] wq#511 [lpp-mp] M5c-3 continuation",
         "detail": "Verify repo: /home/lumen/pokemon"}
    assert router.repo_mismatch(t) is None
    assert router.repo_for_ticket(t) == "/home/lumen/pokemon"


def test_repo_for_ticket_refuses_mismatched_ticket_rather_than_returning_lane_repo():
    t = {"lane": "build", "title": "LPP-MP M5 (stretch): BGB link-over-TCP bridge", "detail": ""}
    assert router.repo_for_ticket(t) is None


def test_dispatch_cmd_raises_on_lane_repo_mismatch(monkeypatch):
    monkeypatch.setattr(router, "wq_show", lambda tid: {
        "id": str(tid), "lane": "build", "detail": "",
        "title": "LPP-MP M5 (stretch): BGB link-over-TCP bridge",
        "model": "claude-sonnet-5", "effort": "high", "archetype": "standard-engineering",
    })
    with pytest.raises(SystemExit, match="lane/repo mismatch"):
        router.dispatch_cmd(433)


def test_dispatch_cmd_explicit_repo_still_overrides_a_mismatch(monkeypatch):
    monkeypatch.setattr(router, "wq_show", lambda tid: {
        "id": str(tid), "lane": "build", "detail": "",
        "title": "LPP-MP M5 (stretch): BGB link-over-TCP bridge",
        "model": "claude-sonnet-5", "effort": "high", "archetype": "standard-engineering",
    })
    cmd = router.dispatch_cmd(433, "/home/lumen/pokemon")
    assert cmd[cmd.index("--repo") + 1] == "/home/lumen/pokemon"


def test_title_repo_markers_agree_with_lane_repo_map():
    """A marker must never point somewhere LANE_REPO does not also recognise, or the
    guard would refuse a dispatch nothing could then satisfy."""
    known = set(router.LANE_REPO.values())
    for marker, repo in router.TITLE_REPO_MARKERS.items():
        assert repo in known, f"{marker} -> {repo} is not a LANE_REPO destination"


# ---- wq#1331 (WORKER_EXEC_CAPABILITY_GAP.md §2.3/§3.2): needs:repo is the per-ticket
# repo override, validated against managed_repos(); TITLE_REPO_MARKERS is derived from
# LANE_REPO at import so the two tables cannot drift (the wq#1325 nest entry did). ------

def test_needs_repo_overrides_lane():
    t = {"lane": "build", "title": "fix(build.sh): flash path", "detail": "",
         "needs": "repo(/home/lumen/questfree)"}
    assert router.repo_for_ticket(t) == "/home/lumen/questfree"


def test_needs_repo_line_in_detail_also_resolves():
    t = {"lane": "build", "title": "fix(build.sh): flash path",
         "detail": "prose\nNeeds: repo(/home/lumen/questfree)\nmore"}
    assert router.repo_for_ticket(t) == "/home/lumen/questfree"


def test_needs_repo_unmanaged_path_falls_through_never_trusted():
    t = {"lane": "build", "title": "x", "detail": "", "needs": "repo(/home/lumen/evil)"}
    assert router.repo_for_ticket(t) == router.FOREMAN_ROOT


def test_needs_repo_beats_verify_hint():
    t = {"lane": "build", "title": "[verify] wq#1173 add mailbot lane routing",
         "detail": "Verify repo: /home/lumen/mailbot",
         "needs": "repo(/home/lumen/questfree)"}
    assert router.repo_for_ticket(t) == "/home/lumen/questfree"


def test_mislaned_ticket_refused_even_with_needs_repo():
    # §5.2 test-to-failure: lane=build + [questfree] title + needs:repo(questfree)
    # must REFUSE (re-lane the ticket), not let the tag paper over the mis-lane...
    t = {"lane": "build", "title": "[questfree] Quest 1 DTS", "detail": "",
         "needs": "repo(/home/lumen/questfree)"}
    assert router.repo_mismatch(t)
    assert router.repo_for_ticket(t) is None


def test_mislaned_ticket_resolves_after_relane():
    # ...and once the lane is fixed, the same ticket resolves.
    t = {"lane": "questfree", "title": "[questfree] Quest 1 DTS", "detail": "",
         "needs": "repo(/home/lumen/questfree)"}
    assert router.repo_mismatch(t) is None
    assert router.repo_for_ticket(t) == "/home/lumen/questfree"


def test_needs_disable_env_makes_resolver_ignore_needs(monkeypatch):
    monkeypatch.setenv("FOREMAN_NEEDS_DISABLE", "1")
    t = {"lane": "build", "title": "x", "detail": "",
         "needs": "repo(/home/lumen/questfree)"}
    assert router.repo_for_ticket(t) == router.FOREMAN_ROOT


def test_title_repo_markers_derived_from_lane_repo():
    # every non-foreman lane is a marker; lpp-mp is the one extra alias (wq#433's
    # own title prefix); no marker may disagree with LANE_REPO.
    expected = {lane: path for lane, path in router.LANE_REPO.items()
                if path != router.FOREMAN_ROOT}
    expected["lpp-mp"] = router.LANE_REPO["pokemon"]
    assert router.TITLE_REPO_MARKERS == expected
    for key in ("lpp", "lpp-mp", "pokemon", "nest", "questfree", "mailbot",
                "pstack", "mission-control"):
        assert key in router.TITLE_REPO_MARKERS, key
    assert router.TITLE_REPO_MARKERS["nest"] == \
        "/home/lumen/projects/nest/NoLongerEvil-Thermostat"  # wq#1325


def test_governor_pressure_missing_bucket_on_zero_floor_pool_is_not_deny(tmp_path):
    """wq#1737: no limits at all (governor allowed on zeroed floors while the usage
    poll is stale/never good) must not turn into 'deny' here; with a floor set the
    missing bucket still denies."""
    gov = tmp_path / "gov.py"
    gov.write_text("import sys; sys.exit(0)\n")
    floors = tmp_path / "floors.json"
    floors.write_text(json.dumps({
        "session_min_remaining_pct": 0, "weekly_all_min_remaining_pct": 0,
        "fable_weekly_min_remaining_pct": 0, "soft_margin_pct": 0}))
    st = tmp_path / "state.json"
    st.write_text(json.dumps({"limits": None, "stale": True}))
    assert router.governor_pressure("claude-sonnet-5", state_path=str(st), governor_cli=str(gov)) == "ok"
    assert router.governor_pressure("claude-fable-5", state_path=str(st), governor_cli=str(gov)) == "ok"
    floors.write_text(json.dumps({"session_min_remaining_pct": 20, "weekly_all_min_remaining_pct": 0,
                                  "fable_weekly_min_remaining_pct": 0, "soft_margin_pct": 0}))
    assert router.governor_pressure("claude-sonnet-5", state_path=str(st), governor_cli=str(gov)) == "deny"


# wq#2581: is_local_rung is a deliberate mirror of harness/fworker.py's own copy (see
# router.py's comment on LOCAL_RUNG_MODELS for why it isn't imported/shared). This test is
# the pin: if fworker.py's copy ever changes, this fails loudly instead of the two silently
# drifting apart -- same pattern as test_token_engine_mirror_agrees in test_masking.py.

def _fworker_local_rung_models():
    harness_dir = os.path.join(ORCH_DIR, "..", "harness")
    sys.path.insert(0, harness_dir)
    import fworker  # noqa: PLC0415
    return fworker.LOCAL_RUNG_MODELS


def test_local_rung_models_mirrors_fworker():
    assert set(router.LOCAL_RUNG_MODELS) == set(_fworker_local_rung_models())


def test_is_local_rung_matches_fworker_is_local_rung():
    harness_dir = os.path.join(ORCH_DIR, "..", "harness")
    sys.path.insert(0, harness_dir)
    import fworker  # noqa: PLC0415
    for m in ("qwen36-local", "nephthys-qwen14b", "hathor-llama8b", "local-worker",
              "deepseek-v4-flash", "claude-sonnet-5", "claude-opus-5", "", None,
              "qwen36-local-something-suffixed"):
        assert router.is_local_rung(m) == fworker.is_local_rung(m), m


# wq#2581: router.escalate_off_local -- hands an exhausted local-rung ticket to the next
# non-local rung in its archetype chain instead of leaving it for a caller to block.

def _stub_wq_show(monkeypatch, ticket):
    monkeypatch.setattr(router, "wq_show", lambda tid: dict(ticket))


def _capture_wq_calls(monkeypatch):
    calls = []

    def fake_wq(*args):
        calls.append(args)
        class R:
            returncode = 0
            stdout = "edited" if args[0] == "edit" else ""
            stderr = ""
        return R()
    monkeypatch.setattr(router, "wq", fake_wq)
    return calls


def test_escalate_off_local_writes_next_rung_and_releases(monkeypatch):
    ticket = {"id": 9001, "archetype": "mechanical-edit", "notes": ""}
    _stub_wq_show(monkeypatch, ticket)
    calls = _capture_wq_calls(monkeypatch)
    manifest = router.load_manifest()

    escalated, detail = router.escalate_off_local(9001, reason="FALSE_COMPLETE",
                                                   manifest=manifest)

    assert escalated is True
    assert "@" in detail
    kinds = [c[0] for c in calls]
    assert "edit" in kinds
    assert "note" in kinds
    assert "release" in kinds
    edit_call = next(c for c in calls if c[0] == "edit")
    assert "--model" in edit_call
    model = edit_call[edit_call.index("--model") + 1]
    assert not router.is_local_rung(model), (
        f"escalate_off_local must never write a local model back, got {model!r}")
    note_call = next(c for c in calls if c[0] == "note")
    assert any(router.ESCALATED_MARKER in a for a in note_call)


def test_escalate_off_local_is_idempotent_per_ticket(monkeypatch):
    ticket = {"id": 9002, "archetype": "mechanical-edit",
              "notes": f"{router.ESCALATED_MARKER}claude-sonnet-5@low after FALSE_COMPLETE"}
    _stub_wq_show(monkeypatch, ticket)
    calls = _capture_wq_calls(monkeypatch)

    escalated, detail = router.escalate_off_local(9002, reason="FALSE_COMPLETE")

    assert escalated is False
    assert "already escalated" in detail
    assert calls == []  # no wq edit/note/release fired the second time


def test_escalate_off_local_declines_with_no_archetype(monkeypatch):
    ticket = {"id": 9003, "archetype": None, "notes": ""}
    _stub_wq_show(monkeypatch, ticket)
    calls = _capture_wq_calls(monkeypatch)

    escalated, detail = router.escalate_off_local(9003, reason="FALSE_COMPLETE")

    assert escalated is False
    assert "archetype" in detail
    assert calls == []
