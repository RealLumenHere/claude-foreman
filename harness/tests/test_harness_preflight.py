"""ISSUE-SOLUTIONS-fable.md §2c — harness-preflight + dispatch stamp gate. Zero quota:
the live-proof layer's `claude -p` spawn is stubbed via the injectable `runner` param,
same pattern fworker's own tests use to avoid spending Anthropic quota in CI.
"""
import json
import os
import shutil
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import harness_preflight  # noqa: E402
import permission_profiles  # noqa: E402

SETTINGS = os.path.join(HARNESS_DIR, "worker-settings.json")
FIXTURE_REPO = "/home/lumen/wq1259-ts-fixture-repo"
FIXTURE_NAME = "wq1259-ts-fixture-repo"


def _clean_runner(cmd, cwd):
    """Simulates a live proof with zero permission denials."""
    return [{"type": "user", "message": {"content": [
        {"type": "tool_result", "is_error": False, "content": "ok"}]}}]


def _denying_runner(cmd, cwd):
    return [{"type": "user", "message": {"content": [
        {"type": "tool_result", "is_error": True,
         "content": "Claude requested permissions to use Bash, but you haven't granted "
                     "it yet. This command requires approval: npx tsc --noEmit"}]}}]


@pytest.fixture
def fixture_conf(tmp_path):
    conf_path = os.path.join(permission_profiles.REPOS_DIR, f"{FIXTURE_NAME}.json")
    stamp_dir = str(tmp_path / "stamps")
    render_dir = str(tmp_path / "render")

    def write(profiles, extra_allow=None):
        with open(conf_path, "w") as f:
            json.dump({"profiles": profiles, "extra_allow": extra_allow or [],
                       "preflight_cmds": ["npx tsc --noEmit"], "verbs": []}, f)

    yield write, stamp_dir, render_dir
    if os.path.isfile(conf_path):
        os.remove(conf_path)
    shutil.rmtree(stamp_dir, ignore_errors=True)
    shutil.rmtree(render_dir, ignore_errors=True)


def test_split_segments_respects_pipes_and_quotes():
    assert harness_preflight.split_segments("git status | tail -5 | grep -c .") == [
        "git status", "tail -5", "grep -c ."]
    assert harness_preflight.split_segments("npm test && echo done") == [
        "npm test", "echo done"]


def test_static_lint_fails_naming_missing_npx_rule(fixture_conf):
    write, stamp_dir, render_dir = fixture_conf
    write(["base", "python"])

    result = harness_preflight.preflight(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir,
        runner=_clean_runner)

    assert result.ok is False
    assert result.reason == "static lint failed"
    assert any("npx" in f for f in result.static_failures)
    assert harness_preflight.read_stamp(FIXTURE_REPO, stamp_dir=stamp_dir) is None


def test_both_layers_pass_and_stamp_written_once_node_ts_added(fixture_conf):
    write, stamp_dir, render_dir = fixture_conf
    write(["base", "python", "node-ts"])

    result = harness_preflight.preflight(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir,
        runner=_clean_runner)

    assert result.ok is True
    assert result.static_failures == []
    assert result.denials == []
    stamp = harness_preflight.read_stamp(FIXTURE_REPO, stamp_dir=stamp_dir)
    assert stamp is not None
    assert stamp["hash"] == result.digest

    ok, reason = harness_preflight.dispatch_gate_check(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir)
    assert (ok, reason) == (True, None)


def test_live_proof_denials_block_stamp_even_when_static_lint_clean(fixture_conf):
    write, stamp_dir, render_dir = fixture_conf
    write(["base", "python", "node-ts"])

    result = harness_preflight.preflight(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir,
        runner=_denying_runner)

    assert result.ok is False
    assert result.reason == "live proof found permission denials"
    assert any("requires approval" in d for d in result.denials)
    assert harness_preflight.read_stamp(FIXTURE_REPO, stamp_dir=stamp_dir) is None


def test_live_proof_filenotfound_returns_ok_false_instead_of_raising(fixture_conf):
    """wq#1384: claude_bin missing from PATH raises FileNotFoundError (an OSError
    subclass) out of subprocess.run inside run_live_proof — preflight() must catch it
    like every other subprocess.run in this module, not let it propagate to the caller."""
    write, stamp_dir, render_dir = fixture_conf
    write(["base", "python", "node-ts"])

    def _missing_binary_runner(cmd, cwd):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])

    result = harness_preflight.preflight(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir,
        runner=_missing_binary_runner)

    assert result.ok is False
    assert "live proof errored" in result.reason
    assert harness_preflight.read_stamp(FIXTURE_REPO, stamp_dir=stamp_dir) is None


def test_dispatch_gate_refuses_unstamped_repo(fixture_conf):
    write, stamp_dir, render_dir = fixture_conf
    write(["base", "python", "node-ts"])

    ok, reason = harness_preflight.dispatch_gate_check(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir)
    assert ok is False
    assert "no preflight stamp" in reason


def test_dispatch_gate_refuses_until_reproof_after_mutation(fixture_conf):
    write, stamp_dir, render_dir = fixture_conf
    write(["base", "python", "node-ts"])
    result = harness_preflight.preflight(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir,
        runner=_clean_runner)
    assert result.ok is True

    # mutate the repo's declared settings (extra_allow) without re-running preflight
    write(["base", "python", "node-ts"], extra_allow=["Bash(some-new-tool *)"])

    ok, reason = harness_preflight.dispatch_gate_check(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir)
    assert ok is False
    assert "stale preflight stamp" in reason

    # re-proof restores a fresh stamp
    result2 = harness_preflight.preflight(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir,
        runner=_clean_runner)
    assert result2.ok is True
    ok, reason = harness_preflight.dispatch_gate_check(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir)
    assert (ok, reason) == (True, None)


def test_unmigrated_repo_is_not_gated():
    ok, reason = harness_preflight.dispatch_gate_check("/home/lumen/no-such-repo-configured")
    assert (ok, reason) == (True, None)
    result = harness_preflight.preflight("/home/lumen/no-such-repo-configured")
    assert result.ok is False
    assert "not migrated to profiles" in result.reason


def test_preflight_commands_include_fleet_exec_probes(fixture_conf):
    """wq#1332 (§3.2 point 4): one representative command per v1 fleet-exec verb is
    always proved, repo-agnostic, the same way INSPECTION_PIPE_PROBE always is —
    the allow rule is unconditional, so this doesn't need a needs:-tagged repo."""
    write, _stamp_dir, _render_dir = fixture_conf
    write(["node"])
    conf = harness_preflight.load_repo_config(FIXTURE_REPO)
    cmds = harness_preflight.preflight_commands(FIXTURE_REPO, conf)
    for probe in harness_preflight.FLEET_EXEC_PREFLIGHT_PROBES:
        assert probe in cmds


def test_fleet_exec_probes_pass_static_lint_against_real_worker_settings():
    """Proves the worker-settings.json allow rule this PR adds actually covers the
    fleet-exec shapes harness-preflight now proves for every needs:-tagged repo."""
    with open(SETTINGS) as f:
        settings = json.load(f)
    perms = settings["permissions"]
    failures = harness_preflight.static_lint(
        harness_preflight.FLEET_EXEC_PREFLIGHT_PROBES, perms["allow"], perms.get("deny", []))
    assert failures == []


# ---- wq#1409: profile-only digest + soft/hard stale --------------------------------

def _settings_variant(tmp_path, mutate):
    """Copy the real worker-settings.json, apply `mutate(dict)`, return the path."""
    with open(SETTINGS) as f:
        d = json.load(f)
    mutate(d)
    p = tmp_path / "worker-settings-variant.json"
    p.write_text(json.dumps(d))
    return str(p)


def _render(settings_file, render_dir):
    conf = harness_preflight.load_repo_config(FIXTURE_REPO)
    _p, rendered, cmds = harness_preflight._rendered_settings_and_cmds(
        FIXTURE_REPO, conf, settings_file, render_dir)
    return rendered, cmds


def test_profile_digest_ignores_hooks_rule_order_and_key_order(fixture_conf, tmp_path):
    """(i) a hook edit, an allow/deny reorder and a docstring-class change (any non-
    permission key) must all leave profile_digest unchanged — that is the whole class
    of churn that staled 760 dispatches in 26h."""
    write, _stamp_dir, render_dir = fixture_conf
    write(["base", "python", "node-ts"])
    rendered, cmds = _render(SETTINGS, render_dir)
    base = harness_preflight.profile_digest(rendered, cmds)
    full = harness_preflight.preflight_hash(rendered, cmds)

    hooked = json.loads(json.dumps(rendered))
    hooked.setdefault("hooks", {})["PreToolUse"] = [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "/bin/true"}]}]
    assert harness_preflight.profile_digest(hooked, cmds) == base
    assert harness_preflight.preflight_hash(hooked, cmds) != full

    reordered = json.loads(json.dumps(rendered))
    reordered["permissions"]["allow"] = list(reversed(reordered["permissions"]["allow"]))
    reordered["permissions"]["deny"] = list(reversed(reordered["permissions"]["deny"]))
    assert harness_preflight.profile_digest(reordered, cmds) == base
    assert harness_preflight.preflight_hash(reordered, cmds) != full

    duplicated = json.loads(json.dumps(rendered))
    duplicated["permissions"]["allow"] += duplicated["permissions"]["allow"][:2]
    assert harness_preflight.profile_digest(duplicated, cmds) == base

    annotated = json.loads(json.dumps(rendered))
    annotated["_comment"] = "docstring-class edit"
    annotated["model"] = "some-other-model"
    assert harness_preflight.profile_digest(annotated, cmds) == base

    assert harness_preflight.profile_digest(rendered, list(reversed(cmds))) == base
    assert harness_preflight.profile_digest(rendered, cmds + [cmds[0]]) == base


def test_profile_digest_changes_on_allow_deny_mode_or_cmds(fixture_conf):
    """(i-b) the four real inputs each move the digest."""
    write, _stamp_dir, render_dir = fixture_conf
    write(["base", "python", "node-ts"])
    rendered, cmds = _render(SETTINGS, render_dir)
    base = harness_preflight.profile_digest(rendered, cmds)

    v = json.loads(json.dumps(rendered)); v["permissions"]["allow"].append("Bash(newtool *)")
    assert harness_preflight.profile_digest(v, cmds) != base
    v = json.loads(json.dumps(rendered)); v["permissions"]["allow"].pop()
    assert harness_preflight.profile_digest(v, cmds) != base
    v = json.loads(json.dumps(rendered)); v["permissions"]["deny"].append("Bash(rm -rf /)")
    assert harness_preflight.profile_digest(v, cmds) != base
    v = json.loads(json.dumps(rendered)); v["permissions"]["defaultMode"] = "plan"
    assert harness_preflight.profile_digest(v, cmds) != base
    assert harness_preflight.profile_digest(rendered, cmds + ["make lint"]) != base


def test_stamp_records_profile_and_settings_hash(fixture_conf):
    write, stamp_dir, render_dir = fixture_conf
    write(["base", "python", "node-ts"])
    result = harness_preflight.preflight(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir,
        runner=_clean_runner)
    assert result.ok
    stamp = harness_preflight.read_stamp(FIXTURE_REPO, stamp_dir=stamp_dir)
    rendered, cmds = _render(SETTINGS, render_dir)
    assert stamp["profile_hash"] == harness_preflight.profile_digest(rendered, cmds)
    assert stamp["settings_hash"] == harness_preflight.preflight_hash(rendered, cmds)
    assert stamp["hash"] == stamp["profile_hash"]  # existing readers keep the gating value
    assert set(stamp) >= {"repo", "hash", "cmds", "proofed_at"}
    assert result.to_dict()["profile_hash"] == stamp["profile_hash"]


def test_hook_change_is_soft_stale_dispatches_and_reseals(fixture_conf, tmp_path, monkeypatch):
    """(i) end to end: stamp under the real settings, then edit a hook. Gate says ok
    with a soft-stale reason, reseal_stamp refreshes settings_hash, and the gate is
    plain fresh afterwards. No reprove anywhere."""
    write, stamp_dir, render_dir = fixture_conf
    write(["base", "python", "node-ts"])
    assert harness_preflight.preflight(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir,
        runner=_clean_runner).ok
    before = harness_preflight.read_stamp(FIXTURE_REPO, stamp_dir=stamp_dir)

    def add_hook(d):
        d.setdefault("hooks", {})["PreToolUse"] = [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "/bin/true"}]}]
    hooked = _settings_variant(tmp_path, add_hook)

    ok, reason = harness_preflight.dispatch_gate_check(
        FIXTURE_REPO, settings_file=hooked, render_dir=render_dir, stamp_dir=stamp_dir)
    assert ok is True
    assert reason and reason.startswith("stamp_soft_stale")

    def boom(*a, **k):
        raise AssertionError("soft-stale must never spawn a live proof")
    path = harness_preflight.reseal_stamp(
        FIXTURE_REPO, settings_file=hooked, render_dir=render_dir, stamp_dir=stamp_dir)
    assert path
    after = harness_preflight.read_stamp(FIXTURE_REPO, stamp_dir=stamp_dir)
    assert after["profile_hash"] == before["profile_hash"]
    assert after["proofed_at"] == before["proofed_at"]
    assert after["settings_hash"] != before["settings_hash"]
    assert "resealed_at" in after
    assert harness_preflight.dispatch_gate_check(
        FIXTURE_REPO, settings_file=hooked, render_dir=render_dir,
        stamp_dir=stamp_dir) == (True, None)
    # a real reprove was never needed — prove it by making one impossible
    monkeypatch.setattr(harness_preflight, "run_live_proof", boom)
    assert harness_preflight.dispatch_gate_check(
        FIXTURE_REPO, settings_file=hooked, render_dir=render_dir,
        stamp_dir=stamp_dir) == (True, None)


def test_five_benign_edits_zero_reproves(fixture_conf, tmp_path):
    """(iv) rapid benign churn: five settings edits in a row, each gate call soft or
    fresh, never hard-stale, never a reprove."""
    write, stamp_dir, render_dir = fixture_conf
    write(["base", "python", "node-ts"])
    assert harness_preflight.preflight(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir,
        runner=_clean_runner).ok
    for i in range(5):
        def mut(d, i=i):
            d.setdefault("hooks", {})[f"Hook{i}"] = [{"matcher": "*", "hooks": []}]
            d["permissions"]["allow"] = list(reversed(d["permissions"]["allow"]))
        variant = _settings_variant(tmp_path, mut)
        ok, reason = harness_preflight.dispatch_gate_check(
            FIXTURE_REPO, settings_file=variant, render_dir=render_dir, stamp_dir=stamp_dir)
        assert ok is True, (i, reason)
        assert reason is None or reason.startswith("stamp_soft_stale"), (i, reason)
        harness_preflight.reseal_stamp(
            FIXTURE_REPO, settings_file=variant, render_dir=render_dir, stamp_dir=stamp_dir)


def test_real_allow_change_is_hard_stale_and_reseal_refuses(fixture_conf, tmp_path):
    """(i-b)/(iv-regress) a permission set change is hard-stale: gate refuses,
    reseal refuses (it must never bless an unproven profile), reprove is the only way
    out and it goes through the live proof — denials still surface."""
    write, stamp_dir, render_dir = fixture_conf
    write(["base", "python", "node-ts"])
    assert harness_preflight.preflight(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir,
        runner=_clean_runner).ok
    write(["base", "python", "node-ts"], extra_allow=["Bash(some-new-tool *)"])
    ok, reason = harness_preflight.dispatch_gate_check(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir)
    assert ok is False and "stale preflight stamp" in reason
    assert harness_preflight.reseal_stamp(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir) is None
    ok2, _ = harness_preflight.dispatch_gate_check(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir)
    assert ok2 is False
    denied = harness_preflight.preflight(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir,
        runner=_denying_runner)
    assert denied.ok is False and denied.denials
    ok3, _ = harness_preflight.dispatch_gate_check(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir)
    assert ok3 is False  # a denied reprove leaves the old (hard-stale) stamp in place


def test_legacy_stamp_fresh_iff_full_hash_matches(fixture_conf, tmp_path):
    """Rollout: a pre-1409 stamp (only `hash` = full-dict digest) stays fresh while
    the render is byte-identical to what it proved, and is hard-stale (never soft)
    the moment anything moved — it cannot tell benign from real, so it reproves once
    and the reprove writes the new format."""
    write, stamp_dir, render_dir = fixture_conf
    write(["base", "python", "node-ts"])
    rendered, cmds = _render(SETTINGS, render_dir)
    legacy = {"repo": FIXTURE_NAME, "hash": harness_preflight.preflight_hash(rendered, cmds),
              "cmds": cmds, "proofed_at": "2026-09-01T00:00:00Z"}
    harness_preflight._write_stamp_payload(FIXTURE_REPO, legacy, stamp_dir)
    assert harness_preflight.dispatch_gate_check(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir,
        stamp_dir=stamp_dir) == (True, None)

    hooked = _settings_variant(tmp_path, lambda d: d.setdefault("hooks", {}).update(
        {"Stop": [{"matcher": "*", "hooks": []}]}))
    ok, reason = harness_preflight.dispatch_gate_check(
        FIXTURE_REPO, settings_file=hooked, render_dir=render_dir, stamp_dir=stamp_dir)
    assert ok is False and "stale preflight stamp" in reason
    assert harness_preflight.reseal_stamp(
        FIXTURE_REPO, settings_file=hooked, render_dir=render_dir, stamp_dir=stamp_dir) is None
    assert harness_preflight.preflight(
        FIXTURE_REPO, settings_file=hooked, render_dir=render_dir, stamp_dir=stamp_dir,
        runner=_clean_runner).ok
    assert "profile_hash" in harness_preflight.read_stamp(FIXTURE_REPO, stamp_dir=stamp_dir)


def test_stamp_strict_env_restores_full_dict_gating(fixture_conf, tmp_path, monkeypatch):
    """Rollback lever: FOREMAN_STAMP_STRICT=1 -> a hook edit is hard-stale again."""
    write, stamp_dir, render_dir = fixture_conf
    write(["base", "python", "node-ts"])
    monkeypatch.setattr(harness_preflight, "STAMP_STRICT", True)
    assert harness_preflight.preflight(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir, stamp_dir=stamp_dir,
        runner=_clean_runner).ok
    stamp = harness_preflight.read_stamp(FIXTURE_REPO, stamp_dir=stamp_dir)
    assert stamp["hash"] == stamp["settings_hash"] != stamp["profile_hash"]
    assert harness_preflight.dispatch_gate_check(
        FIXTURE_REPO, settings_file=SETTINGS, render_dir=render_dir,
        stamp_dir=stamp_dir) == (True, None)
    hooked = _settings_variant(tmp_path, lambda d: d.setdefault("hooks", {}).update(
        {"Stop": [{"matcher": "*", "hooks": []}]}))
    ok, reason = harness_preflight.dispatch_gate_check(
        FIXTURE_REPO, settings_file=hooked, render_dir=render_dir, stamp_dir=stamp_dir)
    assert ok is False and "stale preflight stamp" in reason


def test_live_proof_spawns_claude_bin_not_bare_name(fixture_conf, monkeypatch):
    """wq#1407: under the dispatcher unit's PATH a bare `claude` is FileNotFoundError.
    The default must be the absolute CLAUDE_BIN, overridable via FOREMAN_CLAUDE_BIN."""
    seen = []

    def runner(cmd, cwd):
        seen.append(cmd[0])
        return _clean_runner(cmd, cwd)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    harness_preflight.run_live_proof(FIXTURE_REPO, SETTINGS, ["true"], runner=runner)
    assert seen == [harness_preflight.CLAUDE_BIN]
    assert os.path.isabs(harness_preflight.CLAUDE_BIN)
    harness_preflight.run_live_proof(FIXTURE_REPO, SETTINGS, ["true"], runner=runner,
                                     claude_bin="/opt/x/claude")
    assert seen[-1] == "/opt/x/claude"


def test_no_bare_claude_spawn_in_preflight_path():
    """wq#1407 (c): grep test — the preflight module and CLI carry no bare "claude"
    spawn/default any more."""
    for rel in ("harness_preflight.py", "harness-preflight"):
        with open(os.path.join(HARNESS_DIR, rel)) as f:
            src = f.read()
        assert 'claude_bin="claude"' not in src, rel
        assert 'default="claude"' not in src, rel
        assert '["claude",' not in src, rel
