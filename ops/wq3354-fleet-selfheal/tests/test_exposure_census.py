"""CLASS 8 — exposure-census against the real harness masking.read, real modes.

Every probe runs with root=tmp_path — outside the parameter's /home/lumen default
(wq#2630: a probe drawn only from the default scope cannot falsify). Values are built
at runtime so no credential-shaped literal sits in this file (house rule)."""
import json
import os

import masking

import exposure_census

# Planted live value / decoy, both ≥3 character classes so only the LABEL gate
# separates them — that is the calibration under test.
VAL = "Zq9" * 6                       # behind a credential-labelled key: harvested
MODEL = "openai/gpt-5o-x2026"         # behind JUDGE_MODEL: must NOT be harvested
RAWTOK = "qX7" * 6                    # whole-file .secrets-style raw token
HARNESS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..",
                                       "harness"))


def _store(tmp_path, extra=""):
    d = tmp_path / "store"
    d.mkdir()
    env = d / ".env"
    env.write_text("API_TOKEN=" + VAL + "\nJUDGE_MODEL=" + MODEL + "\n" + extra)
    env.chmod(0o600)                  # the store itself must not be a census hit
    sec = tmp_path / "sec"
    sec.mkdir(mode=0o700)
    raw = sec / "svc.cred"
    raw.write_text(RAWTOK + "\n")
    raw.chmod(0o600)
    return env


def _cfg(tmp_path, **over):
    os.chmod(tmp_path, 0o755)         # pytest makes tmp_path 0700; the probe tree
    cfg = {"root": str(tmp_path),     # must be traversable for exposure to exist
           "sources": [str(tmp_path / "store" / ".env")],
           "source_globs": [str(tmp_path / "sec") + "/*"],
           "prune": [], "max_file_kb": 2048, "prevalence_cap": 20,
           "max_dir_tickets": 5, "time_cap_s": 600, "harness_dir": HARNESS}
    cfg.update(over)
    return {"exposure": cfg}


def test_harvest_takes_labelled_secrets_and_raw_tokens_not_model_names(tmp_path):
    _store(tmp_path)
    live, lens = exposure_census.harvest(_cfg(tmp_path)["exposure"], masking.read)
    names = set(live.values())
    assert "store/.env:API_TOKEN" in names          # label gate admits the credential
    assert any(n.endswith("sec/svc.cred") for n in names)        # whole-file token
    assert not any("JUDGE_MODEL" in n for n in names)  # census2's flood class is OUT
    assert exposure_census._digest(VAL) in live
    assert exposure_census._digest(MODEL) not in live
    assert VAL not in json.dumps(live) and lens  # digests only, never values


def test_world_reachable_leak_files_one_dir_ticket_names_only(make_ctx, tmp_path):
    _store(tmp_path)
    pub = tmp_path / "pub"
    pub.mkdir(mode=0o755)
    leak = pub / "deploy.log"
    leak.write_text("ctx line\ntoken " + VAL + " used\n")
    leak.chmod(0o644)
    ctx = make_ctx(_cfg(tmp_path), {})
    exposure_census.sweep(ctx)
    [t] = ctx.out.tickets
    assert "exposed: 1 file(s) in " + str(pub) in t["title"]
    assert "deploy.log" in t["detail"] and "store/.env:API_TOKEN" in t["detail"]
    assert VAL not in t["detail"] and "sha12=" in t["detail"]
    assert ctx.out.pages and ctx.out.pages[0][0] == "urgent"
    # wq#3547 regression: the ticket text itself must not read as a credential
    assert [r for r in masking.scan(t["detail"])[1] if not r.get("suppressed")] == []
    # values never persist: not in state, not in any action line
    blob = json.dumps(ctx.state) + "\n".join(ctx.actions)
    assert VAL not in blob and RAWTOK not in blob


def test_unreachable_tree_is_not_an_alarm_0750_home_case(make_ctx, tmp_path,
                                                         monkeypatch):
    monkeypatch.setattr(exposure_census, "group_readers", lambda st: set())
    _store(tmp_path)
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o750)          # world-readable file, non-traversable ancestor
    leak = locked / "conf.bak"
    leak.write_text("x " + VAL + "\n")
    leak.chmod(0o644)
    ctx = make_ctx(_cfg(tmp_path), {})
    exposure_census.sweep(ctx)
    assert ctx.out.tickets == [] and ctx.out.pages == []
    assert not [a for a in ctx.out.alerts if a[0].startswith("exposure")]


def test_group_readable_bak_with_outside_readers_alerts_only(make_ctx, tmp_path,
                                                             monkeypatch):
    monkeypatch.setattr(exposure_census, "group_readers", lambda st: {"backup"})
    _store(tmp_path)
    svc = tmp_path / "svc"
    svc.mkdir(mode=0o755)
    bak = svc / "config.yaml.bak"
    bak.write_text("k: " + VAL + "\n")
    bak.chmod(0o640)
    ctx = make_ctx(_cfg(tmp_path), {})
    exposure_census.sweep(ctx)
    assert ctx.out.tickets == []                       # wq#3456 bar: alert, no ticket
    [key] = [a[0] for a in ctx.out.alerts if a[0].startswith("exposure-group:")]
    assert key == "exposure-group:" + str(svc)
    # a user-private group reaches nobody: silent
    monkeypatch.setattr(exposure_census, "group_readers", lambda st: set())
    ctx2 = make_ctx(_cfg(tmp_path), {})
    exposure_census.sweep(ctx2)
    assert not [a for a in ctx2.out.alerts if a[0].startswith("exposure-group")]


def test_prevalent_digest_is_demoted_to_one_calibration_note(make_ctx, tmp_path):
    _store(tmp_path)
    pub = tmp_path / "pub"
    pub.mkdir(mode=0o755)
    for i in range(4):                # cap=2 below, so 4 files demotes the digest
        f = pub / f"page{i}.md"
        f.write_text("uses " + VAL + "\n")
        f.chmod(0o644)
    ctx = make_ctx(_cfg(tmp_path, prevalence_cap=2), {})
    exposure_census.sweep(ctx)
    assert ctx.out.tickets == []
    [cal] = [a for a in ctx.out.alerts if a[0].startswith("exposure-calibration")]
    assert any("demoted" in n and VAL not in n for n in ctx.actions)


def test_second_sweep_is_silent_and_cleanup_resolves_the_episode(make_ctx, tmp_path):
    _store(tmp_path)
    pub = tmp_path / "pub"
    pub.mkdir(mode=0o755)
    leak = pub / "notes.txt"
    leak.write_text(VAL + "\n")
    leak.chmod(0o644)
    ctx = make_ctx(_cfg(tmp_path), {})
    exposure_census.sweep(ctx)
    assert len(ctx.out.tickets) == 1
    ctx2 = make_ctx(_cfg(tmp_path), ctx.state)
    exposure_census.sweep(ctx2)
    assert ctx2.out.tickets == []                      # same findings: same episode
    leak.unlink()
    ctx3 = make_ctx(_cfg(tmp_path), ctx2.state)
    exposure_census.sweep(ctx3)
    assert "exposure:" + str(pub) not in ctx3.state["tickets"]
    assert ctx3.state["exposure"] == {}


def test_time_capped_partial_sweep_never_resolves_open_episodes(make_ctx, tmp_path):
    _store(tmp_path)
    pub = tmp_path / "pub"
    pub.mkdir(mode=0o755)
    leak = pub / "notes.txt"
    leak.write_text(VAL + "\n")
    leak.chmod(0o644)
    ctx = make_ctx(_cfg(tmp_path), {})
    exposure_census.sweep(ctx)
    assert len(ctx.out.tickets) == 1
    leak.unlink()
    ctx2 = make_ctx(_cfg(tmp_path, time_cap_s=-1), ctx.state)  # everything times out
    exposure_census.sweep(ctx2)
    assert "exposure:" + str(pub) in ctx2.state["tickets"]     # still open
