"""wq#617 T3 orchestrator tier — routing, dominated guard, downgrade doctrine, refusal
awareness, verify-pass + judgment policy. Zero Anthropic quota."""
import collections
import copy
import datetime as dt
import itertools
import json
import os
import sqlite3
import subprocess
import sys

import pytest
import yaml

ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ORCH_DIR)
import router  # noqa: E402
import judgment  # noqa: E402

MANIFEST = os.path.join(ORCH_DIR, "..", "research", "models.yaml")

# BUILD_PLAN §T3 verify: "assign 5 sample tickets across archetypes -> correct (model,effort)"
SAMPLES = [
    ("wire standing checks to systemd timers", "", "fleet", "mechanical-edit", ("claude-sonnet-5", "low")),
    ("probe: CI hygiene for lumen-pr", "", "build", "probe-build", ("claude-sonnet-5", "medium")),
    ("add conflict guard to lumen-pr", "", "build", "standard-engineering", ("claude-sonnet-5", "high")),
    ("audit: why does lpp-watchdog bypass the guard", "", "pokemon", "rca-investigation", ("claude-opus-5", "xhigh")),
    ("design lumen-memoryd new service", "", "memory", "novel-design", ("claude-fable-5", "high")),
    ("research pack: agent farm prior art report", "", "research", "research-synthesis", ("claude-opus-5", "high")),
    ("fresh-eyes verify-pass of wq#600 fix", "", "build", "verify-pass", ("claude-sonnet-5", "high")),
    ("restart the auth service", "", "fleet-fires", "fire-response", ("claude-sonnet-5", "high")),
]


@pytest.fixture(scope="module")
def m():
    return router.load_manifest(MANIFEST, force=True)


def test_manifest_loads_and_self_checks(m):
    assert m.check()
    assert set(router.ARCHETYPES) <= set(m.routing)


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
    # family rule: opus-4-x / sonnet-4-6 while the 5-gen sibling is LIVE
    for old in ("claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6"):
        for e in router.EFFORT_LADDER:
            assert m.is_dominated(old, e), old


def test_dominated_never_selected_exhaustively(m):
    """Every archetype x every pressure combination x every escalation fact: the router
    never emits a dominated (model, effort)."""
    models = ["claude-fable-5", "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]
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


# ---- effort-first downgrade -------------------------------------------------------------

def test_effort_first_downgrade_under_soft_pressure(m):
    r, why = router.select(m, "standard-engineering", pressure=lambda x: "tight")
    assert (r.model, r.effort) == ("claude-sonnet-5", "medium")
    assert any("effort-first" in w for w in why)
    r, _ = router.select(m, "rca-investigation", pressure=lambda x: "tight")
    assert (r.model, r.effort) == ("claude-opus-5", "high")
    # fable has no legal lower effort (low/medium dominated) -> stays high, never medium
    r, why = router.select(m, "novel-design", pressure=lambda x: "tight")
    assert (r.model, r.effort) == ("claude-fable-5", "high")
    assert router.effort_down(m, "claude-fable-5", "high") is None
    # opus-5@low is dominated -> effort_down from medium is None, from high is medium
    assert router.effort_down(m, "claude-opus-5", "medium") is None
    assert router.effort_down(m, "claude-opus-5", "high") == "medium"
    # sonnet-5 on archetype 1-2: xhigh/max dominated so effort_down(max) skips to high
    assert router.effort_down(m, "claude-sonnet-5", "max", "probe-build") == "high"


def test_hard_deny_moves_to_next_model_not_lower_effort(m):
    r, why = router.select(m, "mechanical-edit", pressure=lambda x: "deny" if "sonnet" in x else "ok")
    assert r.model == "deepseek-v4-flash" and r.pool == "local"
    r, _ = router.select(m, "probe-build", pressure=lambda x: "deny" if "sonnet" in x else "ok")
    assert r.model == "claude-haiku-4-5" and r.effort is None
    r, why = router.select(m, "novel-design", pressure=lambda x: "deny")
    assert r is None


def test_escalation_rungs_need_their_facts(m):
    r, _ = router.select(m, "standard-engineering", fails=0)
    assert r.model == "claude-sonnet-5"
    r, _ = router.select(m, "standard-engineering", fails=1)
    assert (r.model, r.effort) == ("claude-opus-5", "medium")
    r, _ = router.select(m, "rca-investigation", fails=1)
    assert r.model == "claude-opus-5"
    r, _ = router.select(m, "rca-investigation", fails=2)
    assert (r.model, r.effort) == ("claude-fable-5", "high")
    r, _ = router.select(m, "rca-investigation", cross_subsystem=True)
    assert r.model == "claude-fable-5"
    r, _ = router.select(m, "verify-pass", contradiction=True)
    assert (r.model, r.effort) == ("claude-opus-5", "high")


def test_effort_ceiling_clamp(m):
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
    assert roster["claude-opus-4-8"]["keep_entitled"] and not roster["claude-opus-4-8"]["routed"]
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
    assert (rung.model, rung.effort) == ("claude-sonnet-5", "high")
    rung, note = judgment.verifier_tuple(m, "claude-sonnet-5")
    assert (rung.model, rung.effort) == ("claude-sonnet-5", "high") and "fresh instance" in note
    # flip prefer_distinct_model -> opus-5@high when the author was sonnet-5
    m2 = copy.copy(m)
    m2.verify_policy = dict(m.verify_policy, prefer_distinct_model=True)
    rung, note = judgment.verifier_tuple(m2, "claude-sonnet-5")
    assert (rung.model, rung.effort) == ("claude-opus-5", "high")


def test_judgment_due_cadence_triggers_and_max_reserved(m):
    now = dt.datetime(2026, 8, 16, 3, 0, tzinfo=dt.timezone.utc)   # Sat 20:00 PT: not quiet hours
    empty = {"last_run_at": None, "last_run_tickets_done": None, "runs": []}
    d = judgment.due(m, now=now, state=empty)
    assert d["due"] and d["tuple"] == "claude-fable-5@high"
    recent = {"last_run_at": (now - dt.timedelta(minutes=30)).isoformat(), "last_run_tickets_done": 10, "runs": []}
    d = judgment.due(m, now=now, state=recent, triggers={"ticket_blocked_twice": [5]})
    assert not d["due"]  # min gap
    older = {"last_run_at": (now - dt.timedelta(hours=2)).isoformat(), "last_run_tickets_done": 10, "runs": []}
    d = judgment.due(m, now=now, state=older, tickets_done=12)
    assert not d["due"]
    d = judgment.due(m, now=now, state=older, tickets_done=18)
    assert d["due"] and "tickets closed" in " ".join(d["why"])
    d = judgment.due(m, now=now, state=older, triggers={"ticket_blocked_twice": [5]})
    assert d["due"] and d["tuple"] == "claude-fable-5@high"
    # hardest-investigation trigger: @max is reserved until CAL-1 measured data exists
    d = judgment.due(m, now=now, state=older, triggers={"rca_cross_subsystem": [7]})
    assert d["tuple"] == "claude-fable-5@high" and any("reserved until CAL-1" in w for w in d["why"])
    m2 = copy.copy(m)
    m2.raw = dict(m.raw, measured=[{"row": "x"}])
    d = judgment.due(m2, now=now, state=older, triggers={"rca_cross_subsystem": [7]})
    assert d["tuple"] == "claude-fable-5@max"
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


def test_lane_repo_map_covers_build_and_auto_with_foreman_root():
    assert router.repo_for_lane("build") == router.FOREMAN_ROOT
    assert router.repo_for_lane("auto") == router.FOREMAN_ROOT
    assert router.repo_for_lane("nonexistent-lane") is None


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


# ---- end-to-end against a scratch wq (needs a wq with the wq#617 columns) --------------

def _wq_supports_tuple(wq_bin):
    p = subprocess.run([wq_bin, "add", "-h"], capture_output=True, text=True)
    return "--model" in p.stdout


def test_assign_and_verify_pass_fire_on_scratch_wq(tmp_path):
    wq_bin = os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")
    if not _wq_supports_tuple(wq_bin):
        pytest.skip(f"{wq_bin} lacks wq#617 --model/--effort (bin PR not landed yet)")
    env = dict(os.environ, WQ_DB=str(tmp_path / "q.db"), WQ_AGENT="t617-pytest",
               FOREMAN_WQ_BIN=wq_bin, FOREMAN_RUNS_ROOT=str(tmp_path / "runs"),
               FOREMAN_STATE_JSON=str(tmp_path / "state.json"),
               FOREMAN_JUDGMENT_STATE=str(tmp_path / "runs" / "judgment.json"))
    R = [sys.executable, os.path.join(ORCH_DIR, "router.py")]
    J = [sys.executable, os.path.join(ORCH_DIR, "judgment.py")]

    def wq(*a):
        return subprocess.run([wq_bin, *a], capture_output=True, text=True, env=env)

    ids = []
    for title, detail, lane, arch, expected in SAMPLES[:5]:
        out = wq("add", "--lane", lane, "--title", title).stdout
        tid = int(out.split()[2]); ids.append((tid, arch, expected))
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
    # dispatch-cmd carries the tuple through to fworker
    cmd = subprocess.run(R + ["dispatch-cmd", str(ids[0][0]), "--repo", "/tmp/x"], capture_output=True, text=True, env=env).stdout
    assert "--model claude-sonnet-5 --effort low --archetype mechanical-edit" in cmd
