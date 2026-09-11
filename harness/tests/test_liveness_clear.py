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
import time

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
    lc = clear_mod(env)
    marker = tmp_path / "done.marker"; marker.write_text("x")
    ids = [seed(env, "frank-cred", "[t-i] cred lane open", f"seed\nLiveness: path_exists({marker})"),
           seed(env, "user", "[t-i] user lane blocked", f"seed\nLiveness: path_exists({marker})", status="blocked"),
           seed(env, "user", "[t-i] user lane frank_gated", f"seed\nLiveness: path_exists({marker})", frank_gated=True)]
    recs = lc.sweep(apply=True, ids=ids, db=env["db"], ledger=env["env"]["FOREMAN_LIVENESS_LEDGER"])
    assert [r["action"] for r in recs] == ["closed"] * 3, recs
    for tid in ids:
        r = row(env, tid)
        assert r["status"] == "done" and r["done_at"]
        assert "[liveness auto-clear wq#1478]" in r["notes"] and "SATISFIED" in r["notes"]
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
    for m in list(sys.modules):
        if m in ("liveness_clear", "fworker", "capability", "liveness", "liveness_sweep"):
            sys.modules.pop(m)
    import liveness_clear  # noqa: F401
    assert "fworker" not in sys.modules, "liveness_clear must never import fworker"
    src = open(os.path.join(HARNESS, "liveness_clear.py")).read()
    for banned in ("policy_grants", "preflight(", "resolve_exec_grant", "grant.json", "wq_close"):
        assert banned not in src, f"liveness_clear references {banned}"
    import capability, needs
    t = {"id": 1, "detail": "Liveness: path_exists(/etc/hostname)", "needs": ""}
    assert capability.policy_grants(needs.parse(t.get("needs"), t["detail"])) == ([], [])


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
    # tagless: warn by default for the 7-day rollout window (wq#1676 item 1) — a
    # hard default broke `wq block`/`wq decide` scaffolding all over the rest of the
    # test suite (fworker_integration, shim_matrix, token_injection, wq1479, wq1539),
    # which is exactly the fleet-wide blast radius the rollout knob exists to avoid.
    p = run(env, "add", "--lane", "user", "--title", "[t-vii] tagless", "--detail", "buy a thing")
    assert p.returncode == 0 and "can never auto-clear" in p.stderr
    # FOREMAN_LIVENESS_CLEAR=strict is the explicit flip (set once the window has
    # passed and callers are updated); =warn is the explicit no-op spelling.
    e = dict(env["env"], FOREMAN_LIVENESS_CLEAR="strict")
    p = subprocess.run([env["wq"], "add", "--lane", "user", "--title", "[t-vii] tagless strict-knob", "--detail", "buy"], env=e, capture_output=True, text=True)
    assert p.returncode == 6
    e = dict(env["env"], FOREMAN_LIVENESS_CLEAR="warn")
    p = subprocess.run([env["wq"], "add", "--lane", "user", "--title", "[t-vii] tagless warn-knob", "--detail", "buy"], env=e, capture_output=True, text=True)
    assert p.returncode == 0 and "can never auto-clear" in p.stderr, p.stdout + p.stderr
    # FOREMAN_LIVENESS_LINT_MODE is the older override, still honored, wins either way
    e = dict(env["env"], FOREMAN_LIVENESS_LINT_MODE="strict")
    p = subprocess.run([env["wq"], "add", "--lane", "user", "--title", "[t-vii] tagless lint-strict", "--detail", "buy"], env=e, capture_output=True, text=True)
    assert p.returncode == 6
    e = dict(env["env"], FOREMAN_LIVENESS_CLEAR="strict", FOREMAN_LIVENESS_LINT_MODE="warn")
    p = subprocess.run([env["wq"], "add", "--lane", "user", "--title", "[t-vii] tagless lint-warn", "--detail", "buy"], env=e, capture_output=True, text=True)
    assert p.returncode == 0
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
    to the default scope and closes under --scope all; a second apply = 0 new closes."""
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
    assert row(env, tid)["status"] == "done" and row(env, kept)["status"] == "blocked"
    closed = [a for a in audit_lines(env) if a["ticket"] == tid and a["action"] == "closed"]
    assert len(closed) == 1 and closed[0]["probes"][0]["verdict"] == "SATISFIED"
    # idempotent: the done ticket has left OPEN_STATUSES, nothing else changes
    recs2 = lc.sweep(scope="all", **kw)
    assert [r["ticket"] for r in recs2] == [kept] and recs2[0]["action"] == "kept"
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


def test_derive_probe():
    lc = __import__("liveness_clear")
    assert lc.derive_probe("blocked on wq#1518 until minted") == "ticket_done(1518)"
    assert lc.derive_probe("see lumen/mailbot/pull/42 for the fix") == "pr_merged(lumen/mailbot, 42, merged, gitea)"
    assert lc.derive_probe("see https://github.com/lumen/mailbot/pull/42") == \
        "pr_merged(lumen/mailbot, 42, merged, github)"
    # a bare absolute path is NOT derived (dropped after the real-data dry-run showed
    # it grabbing incidental, unrelated paths — see derive_probe's docstring)
    assert lc.derive_probe("mint /home/lumen/.secrets/cf-r2.env then wire it up") is None
    assert lc.derive_probe("nothing parseable here") is None


def test_derive_probe_resolves_ledger_credential(tmp_path):
    lc = __import__("liveness_clear")
    ledger = tmp_path / "ledger.yaml"
    ledger.write_text(
        "credentials:\n  gh:\n    path: /home/lumen/.secrets/github-frank.token\n"
        "    verify_url: https://api.github.com/user\n    scheme: bearer\n"
        "    probe: cred_alive(/home/lumen/.secrets/github-frank.token, https://api.github.com/user)\n")
    got = lc.derive_probe("rotate /home/lumen/.secrets/github-frank.token before it expires", ledger=str(ledger))
    assert got == "cred_alive(/home/lumen/.secrets/github-frank.token, https://api.github.com/user)"


def test_untagged_gate_ages_out_to_aq(env, tmp_path, monkeypatch):
    lc = clear_mod(env)
    tid = seed(env, "build", "[t-age] no premise, old", "seed, nothing to probe", status="blocked")
    old = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 10 * 86400))
    con = sqlite3.connect(env["db"]); con.execute("UPDATE tasks SET created_at=? WHERE id=?", (old, tid)); con.commit(); con.close()
    calls = []
    monkeypatch.setattr(lc.subprocess, "run", lambda argv, **k: calls.append(argv))
    recs = lc.sweep(scope="all", apply=True, ids=[tid], db=env["db"], ledger=env["env"]["FOREMAN_LIVENESS_LEDGER"])
    assert recs[0]["verdict"] == "NO_TAG" and recs[0]["action"] == "age_alarmed"
    assert calls and calls[0][:3] == ["aq", "add", "liveness"] and calls[0][3] == f"untagged-gate-{tid}"
    # a second sweep does not re-fire (deduped via state)
    calls.clear()
    lc.sweep(scope="all", apply=True, ids=[tid], db=env["db"], ledger=env["env"]["FOREMAN_LIVENESS_LEDGER"])
    assert not calls


def test_no_probe_stamp_never_ages_out(env, tmp_path, monkeypatch):
    lc = clear_mod(env)
    tid = seed(env, "build", "[t-age] no-probe escape hatch", "seed [NO-PROBE: physical]", status="blocked")
    old = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 10 * 86400))
    con = sqlite3.connect(env["db"]); con.execute("UPDATE tasks SET created_at=? WHERE id=?", (old, tid)); con.commit(); con.close()
    calls = []
    monkeypatch.setattr(lc.subprocess, "run", lambda argv, **k: calls.append(argv))
    recs = lc.sweep(scope="all", apply=True, ids=[tid], db=env["db"], ledger=env["env"]["FOREMAN_LIVENESS_LEDGER"])
    assert recs[0]["verdict"] == "NO_TAG" and recs[0]["action"] == "none"
    assert not calls


def test_sweep_derive_backfills_tagless_gate(env, tmp_path):
    """wq#1676 item 2 backfill: --derive proposes from the ticket's own text; dry-run
    reports without writing, --apply writes the Liveness: line and re-probes it."""
    lc = clear_mod(env)
    done_dep = seed(env, "build", "[t-derive] the dep", "seed")
    assert run(env, "edit", done_dep, "--status", "done").returncode == 0
    tid = seed(env, "build", "[t-derive] tagless but parseable",
               f"blocked on wq#{done_dep} until it lands", status="blocked")
    kw = dict(ids=[tid], db=env["db"], ledger=env["env"]["FOREMAN_LIVENESS_LEDGER"], scope="all")
    recs = lc.sweep(apply=False, derive=True, **kw)
    assert recs[0]["verdict"] == "NO_TAG" and recs[0]["derived_probe"] == f"ticket_done({done_dep})"
    assert "Liveness:" not in row(env, tid)["detail"]  # dry-run: nothing written
    recs = lc.sweep(apply=True, derive=True, **kw)
    assert recs[0]["derived_probe"] == f"ticket_done({done_dep})"
    assert recs[0]["verdict"] == "SATISFIED" and recs[0]["action"] == "closed"
    assert f"Liveness: ticket_done({done_dep})" in row(env, tid)["detail"]
    assert row(env, tid)["status"] == "done"


def test_scope_default_unchanged_for_timer():
    """The timer's bare invocation keeps the frank-gated predicate."""
    import liveness_clear as lc_mod, liveness_sweep
    assert inspect.signature(lc_mod.sweep).parameters["scope"].default == "frank"
    assert inspect.signature(liveness_sweep.sweep).parameters["scope"].default == "frank"
    with pytest.raises(ValueError):
        liveness_sweep.tickets_in_scope("everything")
