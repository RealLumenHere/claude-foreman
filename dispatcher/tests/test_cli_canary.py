"""wq#2527: installed-CLI canary — a rung the CLI can't drive is held, never launched.

The scratch prefix below is an npm-global layout (bin/claude -> lib/node_modules/
@anthropic-ai/claude-code/bin/claude.exe, package.json beside it) whose claude.exe answers the
way the API answered CLI 2.1.276 for claude-opus-5-5 on 2026-09-24 (wq#2525's captured result
frame), so the whole path runs: version read, real subprocess probe, cache, router hold."""
import copy
import json
import os
import sys
import time

import pytest
import yaml

DISPATCHER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(DISPATCHER_DIR)
sys.path.insert(0, DISPATCHER_DIR)
sys.path.insert(0, os.path.join(REPO, "orchestrator"))
import cli_canary  # noqa: E402
import router  # noqa: E402

MANIFEST = os.path.join(REPO, "research", "models.yaml")
OPUS55 = "claude-opus-5-5"

STUB = r'''#!/usr/bin/env python3
import json, os, sys
here = os.path.dirname(os.path.realpath(__file__))
version = json.load(open(os.path.join(here, "..", "package.json")))["version"]
with open(os.environ["STUB_CALLS"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
if sys.argv[1:] == ["--version"]:
    print(f"{version} (Claude Code)"); sys.exit(0)
model = sys.argv[sys.argv.index("--model") + 1]
if model == "claude-opus-5-5" and tuple(map(int, version.split("."))) < (2, 1, 280):
    print(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True,
                      "result": "This model requires Claude Code version 2.1.280 or later "
                                f"(current: {version}). Please upgrade."}))
    sys.exit(1)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "OK"}))
'''


def pin_cli(prefix, version):
    """npm-global layout at `prefix` with claude-code pinned to `version`. -> bin path."""
    pkg = prefix / "lib" / "node_modules" / "@anthropic-ai" / "claude-code"
    (pkg / "bin").mkdir(parents=True, exist_ok=True)
    (pkg / "package.json").write_text(json.dumps({"name": cli_canary.PACKAGE, "version": version}))
    exe = pkg / "bin" / "claude.exe"
    exe.write_text(STUB)
    exe.chmod(0o755)
    (prefix / "bin").mkdir(exist_ok=True)
    link = prefix / "bin" / "claude"
    if not link.exists():
        link.symlink_to(exe)
    return str(link)


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_CALLS", str(tmp_path / "calls.jsonl"))
    monkeypatch.setenv("FOREMAN_OAUTH_TOKEN_FILE", str(tmp_path / "no-token"))
    monkeypatch.delenv("FOREMAN_CLI_CANARY_MODE", raising=False)
    bin_path = pin_cli(tmp_path / "prefix", "2.1.276")
    monkeypatch.setenv("FOREMAN_CLAUDE_BIN", bin_path)
    return tmp_path


def calls(tmp_path):
    p = tmp_path / "calls.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


@pytest.fixture
def manifest():
    """The real manifest minus opus-5-5's min_claude_code, so only the canary can hold it."""
    with open(MANIFEST) as f:
        raw = yaml.safe_load(f)
    raw = copy.deepcopy(raw)
    for m in raw["models"]:
        m.pop("min_claude_code", None)
    return router.Manifest(raw)


# ---- the test to failure: pinned old CLI -> rung held, not crashed ------------------------

def test_old_cli_holds_opus55_and_routes_to_next_rung(cli, manifest):
    assert cli_canary.installed_version() == "2.1.276"
    rung, _ = router.select(manifest, "research-synthesis")
    assert rung.model == OPUS55                   # no evidence yet: nothing held

    res = cli_canary.refresh([OPUS55, "claude-sonnet-5"], "2.1.276")
    assert res[OPUS55]["ok"] is False and res[OPUS55]["needs"] == "2.1.280"
    assert cli_canary.TOO_OLD in res[OPUS55]["reason"]
    assert res["claude-sonnet-5"]["ok"] is True

    rung, reasons = router.select(manifest, "research-synthesis")
    assert rung.spec() == "claude-sonnet-5@high"
    assert any(cli_canary.HOLD_TAG in r and OPUS55 in r for r in reasons)


def test_every_rung_held_is_no_rung_not_a_launch(cli, manifest):
    cli_canary.refresh([OPUS55], "2.1.276")
    rung, reasons = router.select(manifest, "rca-investigation")
    assert rung is None
    assert sum(cli_canary.HOLD_TAG in r for r in reasons) == 2


def test_cli_upgrade_is_a_cache_miss_and_clears_the_hold(cli, manifest, monkeypatch):
    cli_canary.refresh([OPUS55], "2.1.276")
    assert router.select(manifest, "research-synthesis")[0].model != OPUS55
    monkeypatch.setenv("FOREMAN_CLAUDE_BIN", pin_cli(cli / "new", "2.1.282"))
    assert cli_canary.installed_version() == "2.1.282"
    assert router.select(manifest, "research-synthesis")[0].model == OPUS55
    assert cli_canary.refresh([OPUS55], "2.1.282")[OPUS55]["ok"] is True


def test_min_claude_code_holds_without_any_probe(cli):
    m = router.load_manifest(MANIFEST, force=True)       # real manifest: min_claude_code 2.1.280
    rung, reasons = router.select(m, "research-synthesis")
    assert rung.spec() == "claude-sonnet-5@high"
    assert any("needs claude-code >= 2.1.280, installed 2.1.276" in r for r in reasons)
    assert calls(cli) == []                             # held from package.json alone


def test_off_lever_releases_the_hold(cli, manifest, monkeypatch):
    cli_canary.refresh([OPUS55], "2.1.276")
    monkeypatch.setenv("FOREMAN_CLI_CANARY_MODE", "off")
    assert router.select(manifest, "research-synthesis")[0].model == OPUS55


def test_static_local_capacity_lever_keeps_the_canary(cli, manifest, monkeypatch):
    cli_canary.refresh([OPUS55], "2.1.276")
    monkeypatch.setenv("FOREMAN_LOCAL_CAPACITY", "static")
    assert router.select(manifest, "research-synthesis")[0].model != OPUS55


# ---- cache policy -------------------------------------------------------------------------

def test_fresh_entry_is_not_reprobed_stale_one_is(cli):
    cli_canary.refresh(["claude-sonnet-5"], "2.1.276", now=1000.0)
    cli_canary.refresh(["claude-sonnet-5"], "2.1.276", now=1000.0 + cli_canary.TTL_S - 1)
    assert len(calls(cli)) == 1
    cli_canary.refresh(["claude-sonnet-5"], "2.1.276", now=1000.0 + cli_canary.TTL_S + 1)
    assert len(calls(cli)) == 2


def test_inconclusive_probe_is_never_cached(cli):
    res = cli_canary.refresh(["claude-sonnet-5"], "2.1.276",
                             probe_fn=lambda m: (None, "rate_limit_error", None))
    assert res["claude-sonnet-5"]["ok"] is None
    assert cli_canary.held("claude-sonnet-5", version="2.1.276") is None
    assert cli_canary.load()["entries"] == {}


def test_failure_stays_held_past_ttl_until_a_reprobe_passes(cli):
    cli_canary.refresh([OPUS55], "2.1.276", now=1000.0)
    assert cli_canary.held(OPUS55, version="2.1.276")    # ttl doesn't expire a hold
    cli_canary.refresh([OPUS55], "2.1.276", now=1000.0 + cli_canary.TTL_S + 1,
                       probe_fn=lambda m: (True, "ok", None))
    assert cli_canary.held(OPUS55, version="2.1.276") is None


def test_probe_uses_worker_auth_not_api_key(cli, monkeypatch):
    (cli / "tok").write_text("oat-test\n")
    monkeypatch.setenv("FOREMAN_OAUTH_TOKEN_FILE", str(cli / "tok"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    env = cli_canary._probe_env()
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oat-test" and "ANTHROPIC_API_KEY" not in env


def test_probe_argv_is_the_ticket_canary(cli):
    cli_canary.probe("claude-sonnet-5")
    argv = calls(cli)[0]
    assert argv[:2] == ["-p", "reply OK"]
    assert argv[argv.index("--max-turns") + 1] == "1"


# ---- classify -----------------------------------------------------------------------------

@pytest.mark.parametrize("rc,out,err,ok", [
    (0, json.dumps({"is_error": False, "result": "OK"}), "", True),
    (1, json.dumps({"is_error": True, "result": "API Error: 429 rate_limit_error"}), "", None),
    (1, "", "Invalid API key · Please run /login", None),
    (1, json.dumps({"is_error": True, "result": "model not found: claude-x"}), "", False),
    (1, '{"api_error":"claude_code_version_too_old"}', "", False),
    # observed live 2026-09-25 from a worktree cwd: the model turn ran, then reached a tool
    (1, json.dumps({"is_error": True, "subtype": "error_max_turns"}), "", True),
    # ids in the json frame must not read as a 401/529 marker
    (1, json.dumps({"is_error": True, "result": "bad model", "session_id": "a401-529"}), "", False),
])
def test_classify(rc, out, err, ok):
    got, reason, _ = cli_canary.classify(rc, out, err)
    assert got is ok and isinstance(reason, str)


# ---- ladder, fact, drift ------------------------------------------------------------------

def test_ladder_models_are_live_claude_rungs_only():
    models = cli_canary.ladder_models(router.load_manifest(MANIFEST, force=True))
    assert OPUS55 in models and "claude-sonnet-5" in models
    assert all(m.startswith("claude-") for m in models)


def test_version_fact_recorded_once_per_version(cli, monkeypatch):
    log = cli / "ingest.log"
    stub = cli / "lumen_state.py"
    stub.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\n")
    stub.chmod(0o755)
    monkeypatch.setenv("FOREMAN_LUMEN_STATE", str(stub))
    assert cli_canary.record_version_fact("2.1.276") is True
    assert cli_canary.record_version_fact("2.1.276") is False
    assert cli_canary.record_version_fact("2.1.282") is True
    lines = log.read_text().splitlines()
    assert len(lines) == 2 and lines[1].startswith("ingest lumen claude_code_cli 2.1.282")


def test_drift_files_one_row_only_when_a_live_rung_needs_the_upgrade(cli):
    rows = {OPUS55: {"min_claude_code": "2.1.280"}}
    row = cli_canary.drift([OPUS55], "2.1.276", "2.1.282", rows)
    assert row[:2] == ("cli_canary", "claude-code-behind") and "2.1.280" in row[2]
    assert cli_canary.drift([OPUS55], "2.1.282", "2.1.290", rows) is None   # behind, not needed
    assert cli_canary.drift([OPUS55], "2.1.276", "2.1.276", rows) is None   # nothing newer


def test_drift_uses_the_needs_version_a_canary_learned(cli):
    cli_canary.refresh([OPUS55], "2.1.276")
    row = cli_canary.drift([OPUS55], "2.1.276", "2.1.282")
    assert row and f"{OPUS55} needs 2.1.280" in row[3]


def test_file_aq_passes_source_and_key_for_aq_dedup(cli, monkeypatch):
    log = cli / "aq.log"
    stub = cli / "aq"
    stub.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" >> {log}\n")
    stub.chmod(0o755)
    monkeypatch.setenv("FOREMAN_AQ_BIN", str(stub))
    assert cli_canary.file_aq(("cli_canary", "claude-code-behind", "t", "m"))
    assert log.read_text().split("\n")[:3] == ["add", "cli_canary", "claude-code-behind"]


# ---- dispatcher: a pinned tuple waits instead of launching --------------------------------

def test_dispatcher_holds_a_ticket_already_pinned_to_a_held_model(cli, monkeypatch):
    import foreman_dispatcher as fd
    monkeypatch.setattr(fd.router, "load_manifest", lambda *a, **k: router.Manifest(
        yaml.safe_load(open(MANIFEST))))
    assert fd.candidate_model({"model": OPUS55}, 1) == (None, fd.CLI_CANARY_HELD)
    assert fd.candidate_model({"model": "claude-sonnet-5"}, 1) == ("claude-sonnet-5", None)


def test_dispatcher_all_rungs_held_is_a_hold_not_route_unassigned(cli, monkeypatch):
    import foreman_dispatcher as fd
    monkeypatch.setattr(fd.router, "assign", lambda tid, dry_run=True: {
        "rung": None, "reasons": [f"skip x: {cli_canary.HOLD_TAG} {OPUS55} ..."]})
    assert fd.candidate_model({}, 1) == (None, fd.CLI_CANARY_HELD)


def test_route_gate_hold_is_not_terminal(cli, monkeypatch):
    import foreman_dispatcher as fd
    import gates

    class G:
        pass
    g = G()
    g.fd, g.t, g.tid = fd, {"model": OPUS55}, 1
    monkeypatch.setattr(fd, "candidate_model", lambda t, tid: (None, fd.CLI_CANARY_HELD))
    f = gates.Route().check(g)
    assert f.reason == fd.CLI_CANARY_HELD and not f.terminal and not f.parkable
