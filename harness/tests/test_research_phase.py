"""Tests for research_phase.py (wq#991) — the mandatory prior-art search phase.
No network: fleet-search is replaced by an injected fake. Proves a successful
run writes a marker the action_gate accepts, and a total search outage writes
NO marker and exits non-zero (fail-closed)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import research_phase as rp
import action_gate as ag

FAKE_OK = '''\
import sys, json
argv = sys.argv[1:]
sub = next(a for a in argv if a in ("web","reddit","github","hn","so","read"))
if sub == "read":
    print(json.dumps({"source":"read","ok":True,"url":argv[-1],
        "content":"Community solved this. OpenMiko bypasses the cloud lock.",
        "comments":[{"author":"need_4_steve","depth":0,"text":"full flash dump + APK archive here"},
                    {"author":"op","depth":1,"text":"worked, thanks"}],"comment_count":2}))
else:
    q = argv[argv.index(sub)+1]
    url = "https://www.reddit.com/r/hacking/comments/1r7/miko_3/" if sub=="reddit" \\
          else f"https://github.com/need4steve/openmiko" if sub=="github" \\
          else f"https://example.com/{sub}"
    print(json.dumps({"source":sub,"ok":True,"query":q,"results":[
        {"title":f"{sub} result for {q}","url":url,"snippet":"prior art","stars":42},
        {"title":f"{sub} secondary","url":url+"?2","snippet":"more"}]}))
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
    projects.mkdir()
    monkeypatch.setattr(rp, "PROJECTS_ROOT", str(projects))
    monkeypatch.setattr(ag, "PROJECTS_ROOT", str(projects))
    def install(fake_src):
        fake = tmp_path / "fake_search.py"
        fake.write_text(fake_src)
        monkeypatch.setattr(rp, "FLEET_SEARCH_BIN", str(fake))
    return projects, install


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


def test_successful_run_writes_marker_gate_accepts(env):
    projects, install = env
    install(FAKE_OK)
    rc = rp.main(["--project", "miko3"])
    assert rc == 0
    rdir = projects / "miko3" / "research"
    marker = rdir / ag.RESEARCH_MARKER
    prior = rdir / "PRIOR_ART.md"
    assert marker.exists() and prior.exists()
    assert prior.stat().st_size >= ag.MIN_FINDINGS_BYTES
    data = json.loads(marker.read_text())
    assert data["schema"] == ag.RESEARCH_SCHEMA
    assert data["project"] == "miko3"
    assert data["total_results"] > 0
    # the gate must now consider research complete for this project
    assert ag.research_complete({"project": "miko3", "hardware_class": "unrecoverable"}) is True
    # and the top reddit thread's comments were pulled into findings
    assert "need_4_steve" in prior.read_text() or "OpenMiko" in prior.read_text()


def test_total_search_outage_writes_no_marker(env):
    projects, install = env
    install(FAKE_FAIL)
    rc = rp.main(["--project", "miko3"])
    assert rc == 2, "fail-closed: outage must be non-zero"
    rdir = projects / "miko3" / "research"
    assert not (rdir / ag.RESEARCH_MARKER).exists(), "no marker on total outage"
    # gate still considers research incomplete
    assert ag.research_complete({"project": "miko3", "hardware_class": "unrecoverable"}) is False


def test_print_queries_no_side_effects(env, capsys):
    projects, install = env
    install(FAKE_OK)
    rc = rp.main(["--project", "miko3", "--print-queries"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "miko 3" in out
    assert not (projects / "miko3" / "research" / ag.RESEARCH_MARKER).exists()
