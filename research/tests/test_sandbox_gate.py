"""wq#3759: sandbox quality gate + bridge dry-run evidence check (design/wq3755 §3-§4).

The local-bwrap tests really execute the committed fixtures' install/smoke text, so a
smoke_exit here is the fixture's own exit code, not a stub's.
"""
import datetime
import json
import os
import shutil
import sqlite3
import sys

import pytest

RESEARCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RESEARCH)
import bridge  # noqa: E402
import sandbox_gate  # noqa: E402

FIXTURES = os.path.join(RESEARCH, "fixtures", "sandbox")
FAIL = os.path.join(FIXTURES, "fail_smoke.json")
PASS = os.path.join(FIXTURES, "pass_smoke.json")
EGRESS = os.path.join(FIXTURES, "egress_smoke.json")
LOCAL = ["--accept-backend", "local-bwrap"]
needs_bwrap = pytest.mark.skipif(
    not os.path.exists("/usr/bin/bwrap") or sandbox_gate.host_arch() != "amd64",
    reason="local-bwrap fixtures need bubblewrap on an amd64 host")


def _bridge(proposal, evidence_dir, *extra):
    db = os.path.join(str(evidence_dir), "queue.db")
    sqlite3.connect(db).execute("CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, "
                                "detail TEXT, gate_type TEXT, created_at TEXT)")
    return bridge.main(["submit", "--dry-run", proposal, "--evidence-dir", str(evidence_dir),
                        "--db", db, *extra])


@needs_bwrap
def test_failing_smoke_yields_nonzero_and_bridge_refuses(tmp_path, capsys):
    ev = sandbox_gate.gate(FAIL, "local-bwrap", str(tmp_path))
    assert ev["install_exit"] == 0
    assert ev["smoke_exit"] not in (0, None)
    on_disk = json.loads((tmp_path / "fixture-fail-smoke.json").read_text())
    assert set(sandbox_gate.EVIDENCE_FIELDS) <= set(on_disk)
    assert on_disk["smoke_exit"] == ev["smoke_exit"]
    capsys.readouterr()
    assert _bridge(FAIL, tmp_path, *LOCAL) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "refused"
    assert out["reasons"] == [f"smoke_exit={ev['smoke_exit']}"]


@needs_bwrap
def test_passing_smoke_would_submit_and_only_container_by_default(tmp_path, capsys):
    ev = sandbox_gate.gate(PASS, "local-bwrap", str(tmp_path))
    assert (ev["install_exit"], ev["smoke_exit"]) == (0, 0)
    assert _bridge(PASS, tmp_path, *LOCAL) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "would_submit"
    assert _bridge(PASS, tmp_path) == 1
    assert "backend 'local-bwrap'" in capsys.readouterr().out


@needs_bwrap
def test_smoke_phase_has_no_egress(tmp_path):
    ev = sandbox_gate.gate(EGRESS, "local-bwrap", str(tmp_path))
    # install runs the same connect with network: the probe works, so a smoke failure is the cut.
    assert ev["install_exit"] == 0
    # 127 is a missing probe binary (wq#3767: python3 isn't in bookworm-slim), not a blocked socket.
    assert ev["smoke_exit"] not in (0, 127, None)


@needs_bwrap
def test_failed_install_skips_smoke(tmp_path):
    prop = json.loads(open(PASS).read())
    prop["install"] = "exit 7"
    p = tmp_path / "p.json"
    p.write_text(json.dumps(prop))
    ev = sandbox_gate.gate(str(p), "local-bwrap", str(tmp_path / "ev"))
    assert (ev["install_exit"], ev["smoke_exit"]) == (7, None)
    reasons = bridge.refusals(str(p), str(tmp_path / "ev"), ("local-bwrap",))
    assert reasons == ["install_exit=7", "smoke_exit=None"]


@needs_bwrap
def test_stdout_tail_is_masked(tmp_path):
    secret = "gh" "p_" + "Q" * 36
    prop = json.loads(open(PASS).read())
    prop["install"] += f" && echo {secret}"
    prop["smoke"] += f" && echo {secret}"
    p = tmp_path / "p.json"
    p.write_text(json.dumps(prop))
    ev = sandbox_gate.gate(str(p), "local-bwrap", str(tmp_path / "ev"))
    assert ev["smoke_exit"] == 0
    raw = (tmp_path / "ev" / "fixture-pass-smoke.json").read_text()
    assert secret not in raw
    assert "== smoke (exit 0) ==" in ev["stdout_tail"]


@needs_bwrap
def test_edited_proposal_and_stale_evidence_refused(tmp_path):
    p = tmp_path / "p.json"
    shutil.copy(PASS, p)
    sandbox_gate.gate(str(p), "local-bwrap", str(tmp_path / "ev"))
    assert bridge.refusals(str(p), str(tmp_path / "ev"), ("local-bwrap",)) == []
    later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=8)
    assert bridge.refusals(str(p), str(tmp_path / "ev"), ("local-bwrap",), now=later)[0] \
        .startswith("evidence stale")
    p.write_text(p.read_text().replace("widget 1.0'", "widget 1.0' || true"))
    assert bridge.refusals(str(p), str(tmp_path / "ev"), ("local-bwrap",)) == [
        "proposal_sha256 mismatch: proposal edited after gating"]


def test_missing_or_malformed_evidence_refused(tmp_path):
    assert bridge.refusals(FAIL, str(tmp_path))[0].startswith("no evidence:")
    (tmp_path / "fixture-fail-smoke.json").write_text(json.dumps({"smoke_exit": True}))
    reasons = bridge.refusals(FAIL, str(tmp_path))
    assert any(r.startswith("evidence field smoke_exit") for r in reasons)
    assert any(r.startswith("evidence field proposal_sha256") for r in reasons)


def test_local_bwrap_refuses_foreign_arch(tmp_path):
    prop = json.loads(open(PASS).read())
    prop["arch"] = "arm64" if sandbox_gate.host_arch() == "amd64" else "amd64"
    p = tmp_path / "p.json"
    p.write_text(json.dumps(prop))
    with pytest.raises(sandbox_gate.ProposalError, match="host arch"):
        sandbox_gate.gate(str(p), "local-bwrap", str(tmp_path))


@pytest.mark.parametrize("bad", [{"id": "../x"}, {"arch": "mips"}, {"smoke": ""}])
def test_proposal_validation(tmp_path, bad):
    prop = {**json.loads(open(PASS).read()), **bad}
    p = tmp_path / "p.json"
    p.write_text(json.dumps(prop))
    with pytest.raises(sandbox_gate.ProposalError):
        sandbox_gate.load_proposal(str(p))


def test_container_argvs_cut_egress_and_pass_nothing_in():
    prop = {**json.loads(open(PASS).read()), "arch": "arm64"}
    argvs, image = sandbox_gate.container_argvs(prop, "t1")
    assert image == sandbox_gate.IMAGES["arm64"]
    install, smoke = argvs["install"], argvs["smoke"]
    assert install[install.index("--network") + 1] == "bridge"
    assert smoke[smoke.index("--network") + 1] == "none"
    for argv in (install, smoke):
        assert argv[argv.index("--platform") + 1] == "linux/arm64"
        assert not {"-v", "--volume", "--mount", "-e", "--env", "--env-file",
                    "--privileged", "--network=host"} & set(argv)
        assert "no-new-privileges" in argv
    assert install[-1] == prop["install"] and smoke[-1] == prop["smoke"]
    assert smoke[smoke.index("--network") + 2:].count("rsg-img:t1") == 1
    assert set(sandbox_gate.CLEAN_ENV) == {"PATH", "HOME", "LANG"}


def test_drain_gates_then_empties_inbox(tmp_path, monkeypatch):
    seen = []

    def fake(prop, deadline):
        seen.append(prop["id"])
        return "fake", 0, "ok\n", 1, "no\n"

    monkeypatch.setitem(sandbox_gate.BACKENDS, "container", fake)
    inbox, gated, ev = tmp_path / "in", tmp_path / "gated", tmp_path / "ev"
    inbox.mkdir()
    shutil.copy(FAIL, inbox / "a.json")
    (inbox / "junk.txt").write_text("x")
    (inbox / "bad.json").write_text("{")
    assert sandbox_gate.drain(str(inbox), str(gated), str(ev), str(tmp_path / "q.db")) == 2
    assert seen == ["fixture-fail-smoke"]
    assert os.listdir(inbox) == []
    assert sorted(os.listdir(gated)) == ["a.json", "bad.json", "junk.txt"]
    assert json.loads((ev / "fixture-fail-smoke.json").read_text())["smoke_exit"] == 1


def test_unit_has_no_secrets_and_outlasts_one_run():
    unit = open(os.path.join(RESEARCH, "systemd", "lumen-research-sandbox.service")).read()
    directives = [ln for ln in unit.splitlines() if ln and not ln.startswith("#")]
    assert not [ln for ln in directives if ln.startswith(("EnvironmentFile", "LoadCredential"))]
    assert "sandbox_gate.py drain" in unit
    timeout = int(unit.split("TimeoutStartSec=")[1].split()[0])
    assert timeout > sandbox_gate.WALL_CLOCK_S
    path = open(os.path.join(RESEARCH, "systemd", "lumen-research-sandbox.path")).read()
    assert "DirectoryNotEmpty=/home/lumen/foreman/research/proposals/pending" in path
