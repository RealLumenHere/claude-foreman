"""Tests for research_phase.py (wq#991) — the prior-art search template.

No network: fleet-search is replaced by an injected fake. Proves the template
produces a PRIOR_ART.md that OPENS the landed wq#1006 device gate, and that a
total search outage writes nothing and exits non-zero (fail-closed), so the
gate stays shut.

Uses a unique temp project name so the gate's hardcoded canonical path
(/home/lumen/projects/<name>/...) cannot exist — the only PRIOR_ART.md in play
is the one the template writes beside the temp rules file."""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import research_phase as rp
import action_gate as ag

PROJ = "faketab991"   # not a real project; keeps the hardcoded canonical path empty

FAKE_OK = '''\
import sys, json
argv = sys.argv[1:]
sub = next(a for a in argv if a in ("web","reddit","github","hn","so","read"))
if sub == "read":
    print(json.dumps({"source":"read","ok":True,"url":argv[-1],
        "content":"Community solved this. OpenMiko-style bypass of the cloud lock.",
        "comments":[{"author":"need_4_steve","depth":0,"text":"full flash dump + APK archive here"},
                    {"author":"op","depth":1,"text":"worked, thanks"}],"comment_count":2}))
else:
    q = argv[argv.index(sub)+1]
    url = "https://www.reddit.com/r/hacking/comments/1r7/thing/" if sub=="reddit" \\
          else "https://github.com/need4steve/openthing" if sub=="github" \\
          else "https://example.com/%s" % sub
    print(json.dumps({"source":sub,"ok":True,"query":q,"results":[
        {"title":"%s result for %s" % (sub,q),"url":url,"snippet":"prior art","stars":42},
        {"title":"%s secondary" % sub,"url":url+"?2","snippet":"more"}]}))
'''

FAKE_FAIL = '''\
import sys, json
argv = sys.argv[1:]
sub = next(a for a in argv if a in ("web","reddit","github","hn","so","read"))
print(json.dumps({"source":sub,"ok":False,"error":"searxng unreachable at 127.0.0.1:8888"}))
sys.exit(2)
'''


@pytest.fixture
def env(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    (projects / PROJ).mkdir(parents=True)
    # the project's rules opt into the research gate (hardware_class + phase_gates)
    (projects / PROJ / ".fworker-rules.yaml").write_text(
        "project: %s\nhardware_class: unrecoverable\nphase_gates: [research, implementation]\n" % PROJ)
    monkeypatch.setattr(rp, "PROJECTS_ROOT", str(projects))

    def install(fake_src):
        fake = tmp_path / "fake_search.py"
        fake.write_text(fake_src)
        monkeypatch.setattr(rp, "FLEET_SEARCH_BIN", str(fake))

    def gate_open():
        """Compile the temp project's rules with the real action_gate and ask
        whether the landed research gate now considers prior art present."""
        out = str(tmp_path / "compiled.json")
        ag.compile_rules([str(projects / PROJ)], out)
        rules = json.load(open(out))
        assert ag._research_gate_required(rules), "temp rules must arm the gate"
        return bool(ag._prior_art_ok(rules))

    return projects, install, gate_open


def test_deslug():
    assert rp.deslug("miko3") == "miko 3"
    assert rp.deslug("rk3568-tablet") == "rk 3568 tablet"
    assert rp.deslug("nest_hub") == "nest hub"


def test_derive_queries_from_project_name():
    q = rp.derive_queries("miko3", {})
    assert any("miko 3" in x for x in q)
    assert any("github" in x for x in q)
    assert 3 <= len(q) <= 6


def test_derive_queries_explicit_rules_win():
    q = rp.derive_queries("miko3", {"research_queries": ["a", "b"]})
    assert q == ["a", "b"]


def test_output_opens_landed_gate(env):
    projects, install, gate_open = env
    install(FAKE_OK)
    assert gate_open() is False, "gate must be shut before any research"
    rc = rp.main(["--project", PROJ])
    assert rc == 0
    prior = projects / PROJ / "research" / "PRIOR_ART.md"
    assert prior.exists() and prior.stat().st_size > 0
    # the top reddit thread's comments were pulled into the findings
    assert "need_4_steve" in prior.read_text()
    # and the landed wq#1006 gate now sees prior art
    assert gate_open() is True, "template output must open the device gate"


def test_total_search_outage_writes_nothing(env):
    projects, install, gate_open = env
    install(FAKE_FAIL)
    rc = rp.main(["--project", PROJ])
    assert rc == 2, "fail-closed: outage must be non-zero"
    prior = projects / PROJ / "research" / "PRIOR_ART.md"
    assert not prior.exists(), "no PRIOR_ART.md on total outage"
    assert gate_open() is False, "gate stays shut after an outage"


def test_print_queries_no_side_effects(env, capsys):
    projects, install, gate_open = env
    install(FAKE_OK)
    rc = rp.main(["--project", PROJ, "--print-queries"])
    assert rc == 0
    assert "faketab 991" in capsys.readouterr().out
    assert not (projects / PROJ / "research" / "PRIOR_ART.md").exists()
    assert gate_open() is False
