"""test_liveness_clear.py — Liveness Gate Slices 3-4 test-to-failure (LIVENESS_GATE.md §12.7).

Runs the REAL wq binary (patched copy) against a scratch queue.db copy under WQ_DB, so
every close goes through the same SQL the fleet uses. Cases (i)-(vii) from the design.
"""
import hashlib
import inspect
import json
import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.dirname(HERE)
REPO = os.path.dirname(HARNESS)
sys.path.insert(0, HARNESS)
sys.path.insert(0, os.path.join(REPO, "dispatcher"))

REAL_WQ = os.environ.get("WQ_BIN_SOURCE", "/home/lumen/bin/wq")
REAL_DB = "/home/lumen/agents/work-queue/queue.db"
PATCH = os.path.join(REPO, "ops", "wq1478-liveness-gate", "apply_wq_patch.py")

# (iv) exec allow/deny path byte-unchanged: pinned 2026-09-08 at PR#343-base + origin/main.
# A drift here is NOT liveness's change — whoever changes these functions re-pins them
# deliberately in their own PR.
PINS = {
    ("capability", "preflight"): "88e287edfbca948d",
    ("capability", "policy_grants"): "16e2717ad09a896d",
    ("capability", "effective_needs"): "41c72c0c490ad732",
    ("capability", "effective_add_dirs"): "27b2e12bab76c1fd",
    ("fworker", "resolve_exec_grant"): "6504c41e248773e1",
    ("fworker", "assert_spawn_capability"): "d360d9dc8186cec6",
}


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    if not (os.path.exists(REAL_WQ) and os.path.exists(REAL_DB)):
        pytest.skip("needs the live wq binary and queue.db")
    d = tmp_path_factory.mktemp("lv")
    wq = str(d / "wq")
    shutil.copy2(REAL_WQ, wq)
    subprocess.run([sys.executable, PATCH, "--wq", wq], check=True, capture_output=True)
    db = str(d / "queue.db")
    src = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True)
    dst = sqlite3.connect(db)
    src.backup(dst)
    dst.execute("DELETE FROM tasks")  # only our seeds
    dst.commit(); src.close(); dst.close()
    rt = str(d / "rt")
    e = dict(os.environ, WQ_DB=db, WQ_BIN=wq, WQ_AGENT="liveness-clear-test",
             FOREMAN_LIVENESS_RUNTIME=rt, FOREMAN_NTFY_BIN="/bin/true",
             FOREMAN_LIVENESS_LEDGER=str(d / "ledger.yaml"), FOREMAN_HARNESS=HARNESS)
    (d / "ledger.yaml").write_text(
        "credentials: {}\ndone_capabilities:\n  marker_done:\n    since: 2026-09-01\n"
        "    evidence: test\n    probe: path_exists(/etc/hostname)\nbindings:\n")
    subprocess.run([wq, "whoami", "--register", "liveness-clear-test"], env=e, capture_output=True)
    return {"wq": wq, "db": db, "env": e, "dir": d, "rt": rt}


def run(env, *args, agent=None):
    e = dict(env["env"])
    if agent:
        e["WQ_AGENT"] = agent
    return subprocess.run([env["wq"]] + [str(a) for a in args], env=e, capture_output=True, text=True)


def seed(env, lane, title, detail, status=None, frank_gated=False, force=True):
    args = ["add", "--lane", lane, "--pri", "2", "--title", title, "--detail", detail]
    if frank_gated:
        args.append("--frank-gated")
    if force:
        args.append("--force")
    p = run(env, *args)
    assert p.returncode == 0, p.stdout + p.stderr
    tid = int(p.stdout.split("added task ")[1].split()[0])
    if status == "blocked":  # user lane is never claimable, so seed the status directly
        assert run(env, "edit", tid, "--status", "blocked").returncode == 0
        assert run(env, "note", tid, "seeded blocked").returncode == 0
    return tid


def row(env, tid):
    return json.loads(run(env, "show", tid, "--json").stdout)


def clear_mod(env):
    for k, v in env["env"].items():
        os.environ[k] = v
    for m in ("liveness_clear", "liveness_sweep", "liveness", "capability"):
        sys.modules.pop(m, None)
    import liveness_clear
    return liveness_clear


def audit_lines(env):
    p = os.path.join(env["rt"], "liveness_audit.jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p) if l.strip()]


# ---------------------------------------------------------------------------

def test_i_seeded_done_gate_autocloses_on_sweep(env, tmp_path):
    """(i) open/frank_gated close --to done as before; (ii) wq#1676: the blocked row
    closes --to open instead — the block premise cleared, its own work is still owed."""
    lc = clear_mod(env)
    marker = tmp_path / "done.marker"; marker.write_text("x")
    ids = [seed(env, "frank-cred", "[t-i] cred lane open", f"seed\nLiveness: path_exists({marker})"),
           seed(env, "user", "[t-i] user lane blocked", f"seed\nLiveness: path_exists({marker})", status="blocked"),
           seed(env, "user", "[t-i] user lane frank_gated", f"seed\nLiveness: path_exists({marker})", frank_gated=True)]
    expect_status = {ids[0]: "done", ids[1]: "open", ids[2]: "done"}
    recs = lc.sweep(apply=True, ids=ids, db=env["db"], ledger=env["env"]["FOREMAN_LIVENESS_LEDGER"])
    assert [r["action"] for r in recs] == ["closed"] * 3, recs
    for tid in ids:
        r = row(env, tid)
        want = expect_status[tid]
        assert r["status"] == want
        assert bool(r["done_at"]) == (want == "done")
        assert "[liveness auto-clear wq#1478]" in r["notes"]
        assert f"-> {want}" in r["notes"] and "SATISFIED" in r["notes"]
        assert "[liveness] auto-clear SATISFIED" in r["notes"]  # the pre-close note survived
    assert {a["ticket"] for a in audit_lines(env) if a["action"] == "closed"} >= set(ids)


def test_ii_genuinely_blocked_stays(env, tmp_path):
    lc = clear_mod(env)
    tid = seed(env, "frank-cred", "[t-ii] still blocked", f"seed\nLiveness: path_exists({tmp_path}/nope)", status="blocked")
    recs = lc.sweep(apply=True, ids=[tid], db=env["db"], ledger=env["env"]["FOREMAN_LIVENESS_LEDGER"])
    assert recs[0]["verdict"] == "STILL_BLOCKED" and recs[0]["action"] == "kept"
    r = row(env, tid)
    assert r["status"] == "blocked" and r["notes"].endswith("seeded blocked")


def test_iii_unknown_stays_and_flags_once(env, monkeypatch):
    lc = clear_mod(env)
    import liveness
    def boom(_arg):
        raise RuntimeError("probe exploded")
    monkeypatch.setitem(liveness.PROBES, "path_exists", boom)
    tid = seed(env, "user", "[t-iii] unknown", "seed\nLiveness: path_exists(/whatever)", frank_gated=True)
    kw = dict(apply=True, ids=[tid], db=env["db"], ledger=env["env"]["FOREMAN_LIVENESS_LEDGER"])
    r1 = lc.sweep(**kw)[0]
    assert r1["verdict"] == "UNKNOWN" and r1["action"] == "flagged"
    r2 = lc.sweep(**kw)[0]
    assert r2["verdict"] == "UNKNOWN" and r2["action"] == "flag_deduped"
    r = row(env, tid)
    assert r["status"] == "frank_gated"
    assert r["notes"].count("[liveness] UNKNOWN") == 1
    assert any(a["ticket"] == tid and a["action"] == "flagged" for a in audit_lines(env))


def test_iii_b_disable_flag_makes_apply_report_only(env, tmp_path, monkeypatch):
    lc = clear_mod(env)
    marker = tmp_path / "m"; marker.write_text("x")
    tid = seed(env, "frank-cred", "[t-iii-b] disabled", f"seed\nLiveness: path_exists({marker})")
    monkeypatch.setenv("FOREMAN_LIVENESS_CLEAR", "0")
    recs = lc.sweep(apply=True, ids=[tid], db=env["db"], ledger=env["env"]["FOREMAN_LIVENESS_LEDGER"])
    assert recs[0]["action"] == "would_close"
    assert row(env, tid)["status"] == "open"


def test_iv_exec_allow_deny_byte_unchanged():
    import capability, fworker
    for (modname, fn), pin in PINS.items():
        mod = {"capability": capability, "fworker": fworker}[modname]
        h = hashlib.sha256(inspect.getsource(getattr(mod, fn)).encode()).hexdigest()[:16]
        assert h == pin, (f"{modname}.{fn} changed ({h} != {pin}) — not liveness's change; "
                          f"re-pin deliberately in the PR that changed it")


def test_iv_firewall_import_surface():
    # wq#1991 (ISSUE-LOG #428): the pop has to be UNDONE. Dropping `fworker` from
    # sys.modules and leaving it dropped is invisible here but fatal downstream —
    # test_token_injection's importlib.reload(fworker) then dies with "module fworker
    # not in sys.modules", 7 failures that only ever appear in a full-suite run.
    saved = {m: sys.modules[m] for m in
             ("liveness_clear", "fworker", "capability", "liveness", "liveness_sweep")
             if m in sys.modules}
    for m in saved:
        sys.modules.pop(m)
    try:
        import liveness_clear  # noqa: F401
        assert "fworker" not in sys.modules, "liveness_clear must never import fworker"
        src = open(os.path.join(HARNESS, "liveness_clear.py")).read()
        for banned in ("policy_grants", "preflight(", "resolve_exec_grant", "grant.json",
                       "wq_close"):
            assert banned not in src, f"liveness_clear references {banned}"
        import capability, needs
        t = {"id": 1, "detail": "Liveness: path_exists(/etc/hostname)", "needs": ""}
        assert capability.policy_grants(needs.parse(t.get("needs"), t["detail"])) == ([], [])
    finally:
        sys.modules.update(saved)


def test_iv_verb_refuses_wrong_identity_and_no_premise(env, tmp_path):
    marker = tmp_path / "m2"; marker.write_text("x")
    tid = seed(env, "frank-cred", "[t-iv] verb guards", f"seed\nLiveness: path_exists({marker})")
    p = run(env, "liveness-close", tid, "--verdict", "path_exists -> SATISFIED", agent="fworker-wq999")
    assert p.returncode != 0 and "liveness-clear identity only" in p.stderr
    tid2 = seed(env, "frank-cred", "[t-iv] no premise", "seed, no liveness line")
    p = run(env, "liveness-close", tid2, "--verdict", "SATISFIED")
    assert p.returncode != 0 and "no Liveness: line" in p.stderr
    p = run(env, "liveness-close", tid, "--verdict", "STILL_BLOCKED: nope")
    assert p.returncode != 0 and "must carry the probe's SATISFIED" in p.stderr
    assert row(env, tid)["status"] == "open" and row(env, tid2)["status"] == "open"


def test_v_replay_session_set(env, monkeypatch):
    """This session's set with the verdicts the live 2026-09-08 sweep returned: only
    lumen-content/blog (1101) is left for Frank."""
    lc = clear_mod(env)
    import liveness
    live = {"repo_is": {"private, RealLumenHere/questfree": "SATISFIED", "private, frankstupak/questfree": "SATISFIED",
                        "private, frankstupak/lumen-content": "STILL_BLOCKED", "private, frankstupak/lumen-blog": "STILL_BLOCKED"},
            "cred_alive": {"/home/lumen/.secrets/github-frank.token, https://api.github.com/user": "SATISFIED"},
            "ts_key_expiry_disabled": {"": "SATISFIED"}, "balance_over": {"cerebras, 0": "SATISFIED"},
            "node_healthy": {"hathor": "SATISFIED"}}
    def fake_run(name, args):
        v = live.get(name, {}).get(args.strip())
        return (v, f"replayed {name}({args})") if v else (liveness.UNKNOWN, "not in replay")
    monkeypatch.setattr(liveness, "run", fake_run)
    bindings = {"902": "repo_is(private, RealLumenHere/questfree), repo_is(private, frankstupak/questfree)",
                "1101": "repo_is(private, frankstupak/lumen-content), repo_is(private, frankstupak/lumen-blog)",
                "1133": "cred_alive(/home/lumen/.secrets/github-frank.token, https://api.github.com/user)",
                "1296": "ts_key_expiry_disabled()", "1047": "balance_over(cerebras, 0)", "362": "node_healthy(hathor)"}
    seeded = {}
    for k, b in bindings.items():
        seeded[k] = seed(env, "frank-cred", f"[t-v] replay {k}", f"seed\nLiveness: {b}")
    state = {}
    left = []
    for k, tid in seeded.items():
        t = row(env, tid)
        rec = lc.clear_if_stale(t, {}, apply=True, state=state)
        if rec["action"] != "closed":
            left.append(k)
    assert left == ["1101"], left
    assert row(env, seeded["1101"])["status"] == "open"


def test_vi_failed_close_leaves_status_and_audits(env, tmp_path, monkeypatch):
    lc = clear_mod(env)
    marker = tmp_path / "m3"; marker.write_text("x")
    tid = seed(env, "user", "[t-vi] close fails", f"seed\nLiveness: path_exists({marker})", frank_gated=True)
    real = lc._wq
    def failing(*args, agent=None):
        if args and args[0] == "liveness-close":
            return subprocess.CompletedProcess(args, 1, "", "REFUSED: simulated")
        return real(*args, agent=agent)
    monkeypatch.setattr(lc, "_wq", failing)
    rec = lc.clear_if_stale(row(env, tid), {}, apply=True, state={})
    assert rec["action"] == "close_failed" and "NOT-CLOSED" in rec["close"]
    assert row(env, tid)["status"] == "frank_gated"
    assert any(a["ticket"] == tid and a["action"] == "close_failed" for a in audit_lines(env))


def test_vii_creation_gate(env, tmp_path, monkeypatch):
    marker = tmp_path / "m4"; marker.write_text("x")
    # SATISFIED probe -> refused rc 6
    p = run(env, "add", "--lane", "user", "--title", "[t-vii] already done", "--detail", f"x\nLiveness: path_exists({marker})")
    assert p.returncode == 6 and "premise already met" in p.stderr, p.stdout + p.stderr
    # ledger-done premise -> refused
    p = run(env, "add", "--lane", "user", "--title", "[t-vii] ledger done", "--detail", "x\nLiveness: path_exists(/etc/hostname)")
    assert p.returncode == 6 and "ledger records" in p.stderr
    # --force files it, on the record
    p = run(env, "add", "--lane", "user", "--title", "[t-vii] forced", "--detail", f"x\nLiveness: path_exists({marker})", "--force")
    assert p.returncode == 0 and "overridden" in p.stderr
    # STILL_BLOCKED files normally
    p = run(env, "add", "--lane", "user", "--title", "[t-vii] blocked ok", "--detail", f"x\nLiveness: path_exists({tmp_path}/missing)")
    assert p.returncode == 0 and "STILL_BLOCKED" in p.stdout
    # wq#1676: tagless refuses by default now (mandatory at the on-ramp)
    p = run(env, "add", "--lane", "user", "--title", "[t-vii] tagless", "--detail", "buy a thing")
    assert p.returncode == 6 and "can never auto-clear" in p.stderr
    # FOREMAN_LIVENESS_CLEAR=warn is the 7-day rollout knob: NO_TAG is warn-only again
    e = dict(env["env"], FOREMAN_LIVENESS_CLEAR="warn")
    p = subprocess.run([env["wq"], "add", "--lane", "user", "--title", "[t-vii] tagless warn", "--detail", "buy"], env=e, capture_output=True, text=True)
    assert p.returncode == 0 and "can never auto-clear" in p.stderr
    # --no-probe is the physical-task escape hatch: files it, stamps the marker, no refusal
    p = run(env, "add", "--lane", "user", "--title", "[t-vii] no-probe", "--detail", "buy a thing",
            "--no-probe", "physical")
    assert p.returncode == 0 and "no-probe" in p.stdout.lower(), p.stdout + p.stderr
    tid = int(p.stdout.split("added task ")[1].split()[0])
    assert "[NO-PROBE: physical]" in row(env, tid)["detail"]
    # --liveness injects the tag before the check; a STILL_BLOCKED premise files normally
    p = run(env, "add", "--lane", "user", "--title", "[t-vii] liveness flag", "--detail", "buy a thing",
            "--liveness", f"path_exists({tmp_path}/missing)")
    assert p.returncode == 0 and "STILL_BLOCKED" in p.stdout
    tid = int(p.stdout.split("added task ")[1].split()[0])
    assert f"Liveness: path_exists({tmp_path}/missing)" in row(env, tid)["detail"]
    # non-gated lane untouched
    p = run(env, "add", "--lane", "build", "--title", "[t-vii] build lane", "--detail", "normal")
    assert p.returncode == 0 and "liveness" not in (p.stdout + p.stderr).lower()
    # decide on a SATISFIED frank_gated ticket refuses (premise met -> not a question)
    tid = seed(env, "user", "[t-vii] decide", f"x\nLiveness: path_exists({marker})", frank_gated=True, force=True)
    p = run(env, "decide", tid, "--prompt", "q?", "--rec", "yes")
    assert p.returncode == 6 and row(env, tid)["status"] == "frank_gated"


def test_dispatch_hook_is_cheap_and_closes_known(env, tmp_path):
    lc = clear_mod(env)
    marker = tmp_path / "m5"; marker.write_text("x")
    tid = seed(env, "frank-cred", "[t-hook] known", f"seed\nLiveness: path_exists({marker})", force=True)
    events = []
    def log(ev, **f): events.append((ev, f))
    fetch = lambda t: row(env, t)
    # unknown to the state file, no ticket passed -> no probe, no fetch
    assert lc.dispatch_hook(tid, None, fetch, log, state_path=str(tmp_path / "s.json")) is False
    assert events == []
    # ticket passed with a Liveness line -> probed and closed
    assert lc.dispatch_hook(tid, row(env, tid), fetch, log, state_path=str(tmp_path / "s.json")) is True
    assert row(env, tid)["status"] == "done"
    assert events and events[0][0] == "liveness" and events[0][1]["action"] == "closed"
    # fresh verdict in state -> no re-probe
    events.clear()
    assert lc.dispatch_hook(tid, row(env, tid), fetch, log, state_path=str(tmp_path / "s.json")) is False
    assert events == []


# ---- wq#1492: --scope all backlog sweep (LIVENESS_GATE.md §13) ---------------------------

def test_scope_all_reaches_non_frank_tickets_and_is_idempotent(env, tmp_path):
    """(i) a build-lane blocked ticket (NOT frank-gated) with a done premise is invisible
    to the default scope and closes under --scope all; wq#1676: it closes --to open (the
    block premise cleared, the ticket's own work is still owed, not done), and its
    Liveness: line is stripped so it does not keep re-satisfying the same probe. A
    second apply finds it NO_TAG (untouched) and 0 new closes."""
    lc = clear_mod(env)
    import liveness_sweep
    marker = tmp_path / "landed"; marker.write_text("x")
    done_dep = seed(env, "build", "[t-scope] the dep", "seed")
    assert run(env, "edit", done_dep, "--status", "done").returncode == 0
    tid = seed(env, "build", "[t-scope] blocked on dep", f"seed\nLiveness: ticket_done({done_dep})", status="blocked")
    kept = seed(env, "build", "[t-scope] still blocked", f"seed\nLiveness: path_exists({tmp_path}/nope)", status="blocked")
    kw = dict(apply=True, ids=[tid, kept], db=env["db"], ledger=env["env"]["FOREMAN_LIVENESS_LEDGER"])
    assert [t["id"] for t in liveness_sweep.frank_gated_tickets(env["db"], [tid, kept])] == []
    assert lc.sweep(**kw) == []                       # default scope never sees a build-lane ticket
    assert row(env, tid)["status"] == "blocked"
    recs = lc.sweep(scope="all", **kw)
    assert {r["ticket"]: r["action"] for r in recs} == {tid: "closed", kept: "kept"}
    assert row(env, tid)["status"] == "open" and row(env, kept)["status"] == "blocked"
    assert "Liveness:" not in row(env, tid)["detail"]
    closed = [a for a in audit_lines(env) if a["ticket"] == tid and a["action"] == "closed"]
    assert len(closed) == 1 and closed[0]["probes"][0]["verdict"] == "SATISFIED"
    # idempotent: the Liveness: line is gone, so a rescan reports NO_TAG (untouched), not
    # a second close — the ticket does not silently complete itself with no work done.
    recs2 = lc.sweep(scope="all", **kw)
    assert {r["ticket"]: r["action"] for r in recs2} == {tid: "none", kept: "kept"}
    assert len([a for a in audit_lines(env) if a["action"] == "closed" and a["ticket"] == tid]) == 1


def test_scope_all_transport_error_is_unknown_flagged_once(env, tmp_path):
    """(iii) a real transport error (bogus verify_url in a test ledger binding, no
    monkeypatch) -> UNKNOWN, ticket untouched, ONE flag note, deduped on re-run."""
    lc = clear_mod(env)
    tok = tmp_path / "tok"; tok.write_text("x")
    ledger = tmp_path / "ledger-bogus.yaml"
    tid = seed(env, "build", "[t-scope-iii] bogus verify url", "seed no line", status="blocked")
    ledger.write_text(f"bindings:\n  {tid}: cred_alive({tok}, http://127.0.0.1:9/verify)\n")
    kw = dict(apply=True, ids=[tid], db=env["db"], ledger=str(ledger), scope="all")
    r1 = lc.sweep(**kw)[0]
    assert r1["verdict"] == "UNKNOWN" and r1["action"] == "flagged"
    assert r1["probes"][0]["detail"].startswith("verify http 0")
    r2 = lc.sweep(**kw)[0]
    assert r2["verdict"] == "UNKNOWN" and r2["action"] == "flag_deduped"
    r = row(env, tid)
    assert r["status"] == "blocked" and r["notes"].count("[liveness] UNKNOWN") == 1
    assert not [a for a in audit_lines(env) if a["ticket"] == tid and a["action"] == "closed"]


def test_scope_all_no_tag_never_closes(env):
    """NO_TAG under --scope all = the ticket is listed, untouched, unaudited."""
    lc = clear_mod(env)
    tid = seed(env, "build", "[t-scope] no premise", "seed, nothing to probe", status="blocked")
    recs = lc.sweep(scope="all", apply=True, ids=[tid], db=env["db"], ledger=env["env"]["FOREMAN_LIVENESS_LEDGER"])
    assert recs[0]["verdict"] == "NO_TAG" and recs[0]["action"] == "none"
    assert row(env, tid)["status"] == "blocked"
    assert not [a for a in audit_lines(env) if a["ticket"] == tid]


def test_scope_default_unchanged_for_timer():
    """The timer's bare invocation keeps the frank-gated predicate."""
    import liveness_clear as lc_mod, liveness_sweep
    assert inspect.signature(lc_mod.sweep).parameters["scope"].default == "frank"
    assert inspect.signature(liveness_sweep.sweep).parameters["scope"].default == "frank"
    with pytest.raises(ValueError):
        liveness_sweep.tickets_in_scope("everything")


# ---- wq#1676: mandatory tag at the on-ramp, block gating, --to open, derivation -----

def test_block_creation_gate_and_liveness_flags(env, tmp_path):
    """wq block was ungated (LIVENESS_GATE.md §3); it shares add/decide's on-ramp, but
    wq#1998 made it ADVISORY there: refusing returns before _finish(a, 'blocked'), so a
    refusal strands the row 'claimed' under a caller that has already given up. An
    untagged block warns and lands; --no-probe and --liveness still stamp the detail."""
    tid = seed(env, "build", "[t-block] needs tag", "seed detail")
    assert run(env, "claim", "--id", tid).returncode == 0
    p = run(env, "block", tid, "waiting on something")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "can never auto-clear" in p.stderr and "NOT enforced at block" in p.stderr
    r = row(env, tid)
    # no [NO-PROBE: ...] stamp on purpose — that marker is what age_alarm() skips on, and
    # an untagged block is exactly the shape it must still file an aq row for.
    assert r["status"] == "blocked" and "[NO-PROBE:" not in r["detail"]

    tid_np = seed(env, "build", "[t-block] no-probe", "seed detail")
    assert run(env, "claim", "--id", tid_np).returncode == 0
    p = run(env, "block", tid_np, "waiting on something", "--no-probe", "physical")
    assert p.returncode == 0, p.stdout + p.stderr
    r_np = row(env, tid_np)
    assert r_np["status"] == "blocked" and "[NO-PROBE: physical]" in r_np["detail"]

    tid2 = seed(env, "build", "[t-block] with tag", "seed detail")
    assert run(env, "claim", "--id", tid2).returncode == 0
    marker = tmp_path / "missing-dep"
    p = run(env, "block", tid2, "waiting on dep", "--liveness", f"path_exists({marker})")
    assert p.returncode == 0
    r2 = row(env, tid2)
    assert r2["status"] == "blocked" and f"Liveness: path_exists({marker})" in r2["detail"]


def test_liveness_close_to_open(env, tmp_path):
    """wq#1676 item 4: --to open only for a currently-blocked row; it goes open (not
    done), claimable, no done_at, notes stamped with the target."""
    marker = tmp_path / "m-open"; marker.write_text("x")
    tid = seed(env, "build", "[t-open] blocked", f"seed\nLiveness: path_exists({marker})", status="blocked")
    tid_fg = seed(env, "user", "[t-open] frank_gated", f"seed\nLiveness: path_exists({marker})", frank_gated=True)
    p = run(env, "liveness-close", tid_fg, "--verdict", "path_exists -> SATISFIED", "--to", "open")
    assert p.returncode != 0 and "--to open only applies to a blocked row" in p.stderr
    assert row(env, tid_fg)["status"] == "frank_gated"
    p = run(env, "liveness-close", tid, "--verdict", "path_exists -> SATISFIED", "--to", "open")
    assert p.returncode == 0, p.stdout + p.stderr
    r = row(env, tid)
    assert r["status"] == "open" and not r["done_at"]
    assert "-> open" in r["notes"] and "SATISFIED" in r["notes"]


def test_derive_probe_unit(tmp_path):
    import liveness_clear as lc_mod
    assert lc_mod.derive_probe("blocked on wq#42") == "ticket_done(42)"
    assert lc_mod.derive_probe("see https://github.com/o/r/pull/7") == "pr_merged(o/r, 7, merged, github)"
    assert lc_mod.derive_probe("see http://gitea.local/o/r/pulls/9") == "pr_merged(o/r, 9)"
    assert lc_mod.derive_probe(f"path at {tmp_path}/thing needs doing") == f"path_exists({tmp_path}/thing)"
    assert lc_mod.derive_probe("nothing parseable here") is None
    ledger = tmp_path / "ledger.yaml"
    ledger.write_text(
        "credentials:\n  gh:\n    path: /home/lumen/.secrets/github-frank.token\n"
        "    verify_url: https://api.github.com/user\n"
        "    probe: cred_alive(/home/lumen/.secrets/github-frank.token, https://api.github.com/user)\n")
    assert lc_mod.derive_probe("token at /home/lumen/.secrets/github-frank.token", str(ledger)) == \
        "cred_alive(/home/lumen/.secrets/github-frank.token, https://api.github.com/user)"
    assert lc_mod.derive_probe("token at /home/lumen/.secrets/unknown.token", str(ledger)) is None


def test_accept_probe_derives_and_writes_tag(env):
    """--accept-probe writes derive_probe()'s proposal exactly as --liveness would."""
    dep = seed(env, "build", "[t-derive] dep", "seed")
    p = run(env, "add", "--lane", "user", "--title", "[t-derive] blocked on dep",
            "--detail", f"blocked on wq#{dep}")
    assert p.returncode == 6 and f"ticket_done({dep})" in p.stderr
    p = run(env, "add", "--lane", "user", "--title", "[t-derive] blocked on dep 2",
            "--detail", f"blocked on wq#{dep}", "--accept-probe")
    assert p.returncode == 0, p.stdout + p.stderr
    tid = int(p.stdout.split("added task ")[1].split()[0])
    assert f"Liveness: ticket_done({dep})" in row(env, tid)["detail"]
