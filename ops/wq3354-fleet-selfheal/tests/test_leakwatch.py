"""CLASS 3 — leakwatch against the real harness masking.scan and real kernel inotify."""
import grp
import os
import pwd

import masking

import leakwatch
import selfheal_sweep

# Built at runtime so no credential-shaped literal sits in this file (house rule).
FAKE_PAT = "gh" + "p_" + "Q" * 36


def _rules(d):
    return [{"dir": str(d), "globs": ["claude_memory.md"], "action": "scan"},
            {"dir": str(d), "globs": [".env", ".env.*"], "action": "chmod"}]


def test_literal_credential_in_a_scan_file_files_one_rotation_ticket(make_ctx, tmp_path):
    f = tmp_path / "claude_memory.md"
    f.write_text("# notes\nsome fact\nthe key: " + FAKE_PAT + "\n")
    before = f.read_text()
    ctx = make_ctx({}, {})
    leakwatch.check(ctx, str(f), _rules(tmp_path)[0], masking.scan)
    [t] = ctx.out.tickets
    assert t["title"].startswith("[selfheal] rotate: 1 literal credential")
    assert FAKE_PAT not in t["detail"] and "sha12=" in t["detail"]
    assert ctx.out.pages[0][0] == "urgent"
    assert f.read_text() == before                      # never edited (instinct 7)
    leakwatch.check(ctx, str(f), _rules(tmp_path)[0], masking.scan)
    assert len(ctx.out.tickets) == 1                    # same value: same episode


def test_ticket_text_for_a_key_labelled_hit_is_not_itself_a_hit(make_ctx, tmp_path):
    # wq#3547: the listing glued `kv-secret LiteLLM key =` to `sha12=<hex>`, so reading
    # the ticket back fired kv-secret on the fingerprint and filed a rotation row.
    f = tmp_path / "claude_memory.md"
    f.write_text("LiteLLM key = " + "Q9" * 10 + " in agent/.env\n")
    ctx = make_ctx({}, {})
    leakwatch.check(ctx, str(f), _rules(tmp_path)[0], masking.scan)
    [t] = ctx.out.tickets
    assert "kv-secret LiteLLM key =" in t["detail"]
    assert [r for r in masking.scan(t["detail"])[1] if not r.get("suppressed")] == []


def test_clean_file_files_nothing_and_cleanup_closes_the_episode(make_ctx, tmp_path):
    f = tmp_path / "claude_memory.md"
    f.write_text("token lives in vault at secret/litellm, never inline\n")
    ctx = make_ctx({}, {})
    leakwatch.check(ctx, str(f), _rules(tmp_path)[0], masking.scan)
    assert ctx.out.tickets == []
    f.write_text("x: " + FAKE_PAT + "\n")
    leakwatch.check(ctx, str(f), _rules(tmp_path)[0], masking.scan)
    assert "leak:" + str(f) in ctx.state["tickets"]
    f.write_text("rotated; pointer only\n")
    leakwatch.check(ctx, str(f), _rules(tmp_path)[0], masking.scan)
    assert "leak:" + str(f) not in ctx.state["tickets"]


def test_world_readable_secret_store_loses_world_bits_group_is_reported(make_ctx, tmp_path,
                                                                       monkeypatch):
    monkeypatch.setattr(leakwatch, "group_readers", lambda st: {"backup"})
    env = tmp_path / ".env.bak.1"
    env.write_text("x=1\n")
    os.chmod(env, 0o644)
    ctx = make_ctx({}, {})
    leakwatch.check(ctx, str(env), _rules(tmp_path)[1], masking.scan)
    assert oct(os.stat(env).st_mode & 0o777) == "0o640"
    assert ctx.out.alerts and ctx.out.tickets == []
    ctx = make_ctx({}, ctx.state)
    leakwatch.check(ctx, str(env), _rules(tmp_path)[1], masking.scan)
    assert oct(os.stat(env).st_mode & 0o777) == "0o640"      # group left alone
    assert any("group-accessible" in a for a in ctx.actions)


def _fake_accounts(monkeypatch, gr_mem, primary):
    """`gr_mem` = group member names; `primary` = names whose primary gid matches this
    process's real egid/euid, so the real tmp_path file (owned by whoever runs pytest,
    lumen or root) still resolves against the fake records (wq#3552: hardcoded uid/gid
    1000 only matched when pytest ran as lumen; root's tmp_path files have uid/gid 0)."""
    uid, gid = os.geteuid(), os.getegid()
    monkeypatch.setattr(leakwatch.grp, "getgrgid",
                        lambda g: grp.struct_group(("lumen", "x", gid, gr_mem)))
    monkeypatch.setattr(leakwatch.pwd, "getpwuid",
                        lambda u: pwd.struct_passwd(("lumen", "x", uid, gid, "", "/", "")))
    monkeypatch.setattr(leakwatch.pwd, "getpwall", lambda: [
        pwd.struct_passwd((n, "x", uid + i, gid, "", "/", "")) for i, n in enumerate(primary)])


def test_user_private_group_is_not_reported(make_ctx, tmp_path, monkeypatch):
    _fake_accounts(monkeypatch, [], ["lumen"])
    bak = tmp_path / ".env.bak.2"
    bak.write_text("x=1\n")
    os.chmod(bak, 0o660)
    ctx = make_ctx({}, {})
    leakwatch.check(ctx, str(bak), _rules(tmp_path)[1], masking.scan)
    assert ctx.out.alerts == [] and ctx.actions == []
    assert str(bak) not in ctx.state.get("leakwatch_group", {})


def test_group_reaching_another_account_is_reported(make_ctx, tmp_path, monkeypatch):
    _fake_accounts(monkeypatch, ["litellm"], ["lumen"])
    bak = tmp_path / ".env.bak.3"
    bak.write_text("x=1\n")
    os.chmod(bak, 0o640)
    ctx = make_ctx({}, {})
    leakwatch.check(ctx, str(bak), _rules(tmp_path)[1], masking.scan)
    assert ctx.out.alerts == [("leak-mode:" + str(bak), str(bak) + " is group-accessible")]
    assert leakwatch.group_readers(os.stat(bak)) == {"litellm"}
    _fake_accounts(monkeypatch, [], ["lumen", "svc"])
    assert leakwatch.group_readers(os.stat(bak)) == {"svc"}
    monkeypatch.setattr(leakwatch.grp, "getgrgid", lambda gid: (_ for _ in ()).throw(KeyError))
    assert leakwatch.group_readers(os.stat(bak)) == {f"gid {os.stat(bak).st_gid}"}


def test_dry_run_changes_no_mode(make_ctx, tmp_path):
    env = tmp_path / ".env"
    env.write_text("x=1\n")
    os.chmod(env, 0o666)
    leakwatch.check(make_ctx({}, {}, dry_run=True), str(env), _rules(tmp_path)[1],
                    masking.scan)
    assert os.stat(env).st_mode & 0o777 == 0o666


def test_real_inotify_sees_a_write_and_a_rename_into_place(tmp_path):
    ino = leakwatch.Inotify()
    ino.watch(str(tmp_path))
    (tmp_path / "claude_memory.md").write_text("hello\n")
    tmp = tmp_path / "x.tmp"
    tmp.write_text("y\n")
    os.replace(tmp, tmp_path / ".env.new")
    events = set()
    for _ in range(5):
        events |= set(ino.read(timeout=0.5))
    names = {n for _, n in events}
    assert {"claude_memory.md", ".env.new"} <= names
    rules = _rules(tmp_path)
    assert leakwatch.rule_for(str(tmp_path / ".env.new"), rules)["action"] == "chmod"
    assert leakwatch.rule_for(str(tmp_path / "x.tmp"), rules) is None


def test_once_mode_end_to_end_on_a_granted_non_cwd_dir(tmp_path, monkeypatch):
    """Drive main() over a config naming a temp dir (outside the bundle and cwd)."""
    (tmp_path / "claude_memory.md").write_text("k: " + FAKE_PAT + "\n")
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(f"leakwatch:\n  harness_dir: {os.path.dirname(masking.__file__)}\n"
                   f"  rules:\n    - dir: {tmp_path}\n      globs: [claude_memory.md]\n"
                   f"      action: scan\n")
    filed = []
    monkeypatch.setattr(leakwatch.common.Outlets, "file_ticket",
                        lambda self, *a: filed.append(a) or "1")
    monkeypatch.setattr(leakwatch.common.Outlets, "alert", lambda self, *a: None)
    monkeypatch.setattr(leakwatch.common.Outlets, "page", lambda self, *a: None)
    state = tmp_path / "state.json"
    assert leakwatch.main(["--config", str(cfg), "--state", str(state), "--once"]) == 0
    assert len(filed) == 1 and state.exists()


def test_sweep_cli_dry_run_isolates_a_crashing_class(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("guards: {enabled: false}\ncrashloop: {enabled: false}\n"
                   "providers: {enabled: false}\nnodes: {enabled: false}\n"
                   "disk: {mounts: ['/definitely/not/here']}\n")
    state = tmp_path / "state.json"
    assert selfheal_sweep.main(["--config", str(cfg), "--state", str(state),
                                "--dry-run"]) == 0
    assert not state.exists()                           # dry-run saves nothing

    def boom(ctx):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(selfheal_sweep.sh_disk, "sweep", boom)
    alerts = []
    monkeypatch.setattr(selfheal_sweep.common.Outlets, "alert",
                        lambda self, *a: alerts.append(a))
    assert selfheal_sweep.main(["--config", str(cfg), "--state", str(state),
                                "--only", "disk,nodes"]) == 0
    assert "CLASS CRASHED" in capsys.readouterr().out and alerts


def test_unchmodable_file_is_reported_and_the_sweep_keeps_going(make_ctx, tmp_path, monkeypatch):
    """Regression (2026-10-06): EPERM on one root-owned backup crash-looped the daemon."""
    bad = tmp_path / ".env.bak.root-owned"
    good = tmp_path / ".env.bak.zmine"
    for f in (bad, good):
        f.write_text("x=1\n")
        os.chmod(f, 0o644)
    real_chmod = os.chmod

    def flaky(path, mode, *a, **k):
        if str(path) == str(bad):
            raise PermissionError(1, "Operation not permitted", str(path))
        return real_chmod(path, mode, *a, **k)
    monkeypatch.setattr(os, "chmod", flaky)
    ctx = make_ctx({}, {})                                   # NOT dry-run: the real mutation path
    leakwatch.initial_sweep(ctx, [_rules(tmp_path)[1]], masking.scan)   # must not raise
    assert os.stat(good).st_mode & 0o007 == 0                # a later file is still secured
    assert os.stat(bad).st_mode & 0o007 != 0                 # the unfixable one is untouched
    assert any(k.startswith("leak-unfixable:") and str(bad) in k for k, _ in ctx.out.alerts)


def test_safe_check_swallows_any_per_file_error(make_ctx, tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("x=1\n")

    def boom(*a, **k):
        raise UnicodeDecodeError("utf-8", b"x", 0, 1, "boom")
    monkeypatch.setattr(leakwatch, "check", boom)
    ctx = make_ctx({}, {})
    leakwatch.safe_check(ctx, str(f), _rules(tmp_path)[1], masking.scan)   # must not raise
    assert ctx.out.alerts
