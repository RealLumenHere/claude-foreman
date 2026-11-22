"""wq#5145 (wq#5041 gap 5): review-readonly permission profile + SARIF emission.
Zero quota: no `claude` child is spawned; wq is stubbed where fworker would call it.
"""
import importlib
import json
import os
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(HARNESS_DIR)
sys.path.insert(0, HARNESS_DIR)
import acceptance  # noqa: E402
import agents_spec  # noqa: E402
import permission_profiles as pp  # noqa: E402
import review_sarif  # noqa: E402

SETTINGS = os.path.join(HARNESS_DIR, "worker-settings.json")


def _block(doc):
    return "Reviewed.\n\n```review-findings\n" + json.dumps(doc) + "\n```\n\nEXIT_SIGNAL: true\nTICKET_CLOSED\n"


GOOD = {"summary": "read harness/x.py",
        "findings": [{"rule_id": "py-shell-injection", "level": "high",
                      "message": "shell=True with ticket text", "path": "./harness/x.py",
                      "start_line": 12, "end_line": 14},
                     {"rule_id": "py-shell-injection", "level": "warning",
                      "message": "second site", "path": "harness/y.py", "start_line": 3},
                     {"message": "file-level note", "path": "README.md", "level": "note"}]}


# ---- permission profile ------------------------------------------------------------

def _base():
    with open(SETTINGS) as f:
        return json.load(f)


def test_profile_allows_only_read_grep_glob_and_denies_exec_and_write():
    base = _base()
    merged = pp.render_review_readonly(base)
    perms = merged["permissions"]
    assert perms["allow"] == ["Read", "Grep", "Glob"]
    assert not any(r.startswith("Bash") for r in perms["allow"])
    for tool in ("Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"):
        assert tool in perms["deny"]
    # credential read-denies from the base pack survive (guard floor not dropped)
    assert "Read(**/.env)" in perms["deny"]
    # hooks pass through unchanged; base_settings is not mutated
    assert merged.get("hooks") == base.get("hooks")
    assert base == _base()
    assert pp.is_review_readonly(merged)


def test_is_review_readonly_rejects_build_shaped_packs():
    # the static worker pack and a rendered repo pack both carry Bash -> not read-only
    assert not pp.is_review_readonly(_base())
    rendered = pp.render_permissions("/home/lumen/foreman", _base())
    assert not pp.is_review_readonly(rendered)
    # a pack with the right allow but a missing deny is not read-only either
    assert not pp.is_review_readonly({"permissions": {"allow": ["Read"], "deny": ["Bash"]}})


def test_fworker_resolves_review_readonly_over_any_repo_profile(tmp_path):
    import fworker
    importlib.reload(fworker)
    fworker.RUNS_ROOT = str(tmp_path)
    # a repo WITH a repos.d profile (foreman) and one without: both get the same pack
    for repo in ("/home/lumen/foreman", "/home/lumen/no-such-repo-configured"):
        path = fworker.resolve_worker_settings(repo, "5145", archetype="review-readonly")
        assert os.path.realpath(path) != os.path.realpath(SETTINGS)
        assert os.path.dirname(path) == os.path.join(str(tmp_path), "wq5145")
        with open(path) as f:
            rendered = json.load(f)
        assert pp.is_review_readonly(rendered)
        assert "Bash(pytest *)" not in rendered["permissions"]["allow"]
    # an ordinary build archetype on the same repo is untouched
    build = fworker.resolve_worker_settings("/home/lumen/foreman", "5145", archetype="build")
    with open(build) as f:
        assert not pp.is_review_readonly(json.load(f))


def test_archetype_is_registered_everywhere():
    spec = agents_spec.load(os.path.join(HARNESS_DIR, "agents.yaml"), force=True)
    entry = spec.get(pp.REVIEW_READONLY_ARCHETYPE)
    assert "review-findings" in (entry.prompt_addendum or "")
    assert review_sarif.ARCHETYPE == pp.REVIEW_READONLY_ARCHETYPE
    sys.path.insert(0, os.path.join(REPO_ROOT, "orchestrator"))
    import router
    assert router.ARCHETYPE_NUMBERS[11] == "review-readonly"
    m = router.load_manifest(force=True)  # Manifest.check() raises on any problem
    assert m.check() is True
    assert [str(r) for r in m.chain("review-readonly")][0] == "claude-opus-5-5@high"


def test_acceptance_pins_no_criteria_for_review_readonly():
    ticket = {"id": 1, "title": "review harness/x.py", "archetype": "review-readonly",
              "detail": "run `pytest harness/tests/test_x.py` afterwards"}
    doc = acceptance.derive(ticket)
    assert doc["criteria"] == []
    # same text on a build archetype still derives criteria (the probe can fail)
    assert acceptance.derive(dict(ticket, archetype="standard-engineering"))["criteria"]


# ---- SARIF -------------------------------------------------------------------------

def test_parse_normalises_levels_paths_and_lines():
    doc = review_sarif.parse_findings(_block(GOOD))
    f0, f1, f2 = doc["findings"]
    assert f0["level"] == "error" and f0["path"] == "harness/x.py"
    assert (f0["start_line"], f0["end_line"]) == (12, 14)
    assert f1["end_line"] is None
    assert f2["rule_id"] == "review-finding" and f2["start_line"] is None


def test_last_block_wins():
    text = _block({"findings": [{"message": "draft", "path": "a"}]}) + _block(GOOD)
    assert len(review_sarif.parse_findings(text)["findings"]) == 3


@pytest.mark.parametrize("text,needle", [
    ("no block here", "no ```review-findings"),
    ("```review-findings\n{not json\n```", "not valid JSON"),
    (None, "no ```review-findings"),
])
def test_parse_rejects_missing_or_bad_block(text, needle):
    with pytest.raises(review_sarif.FindingsError) as e:
        review_sarif.parse_findings(text)
    assert needle in str(e.value)


@pytest.mark.parametrize("finding,needle", [
    ({"path": "a"}, "message is required"),
    ({"message": "m"}, "path is required"),
    ({"message": "m", "path": "a", "level": "fatal"}, "level"),
    ({"message": "m", "path": "a", "start_line": 0}, "1-based"),
    ({"message": "m", "path": "a", "start_line": 9, "end_line": 3}, "end_line 3 < start_line 9"),
])
def test_normalise_rejects_bad_findings(finding, needle):
    with pytest.raises(review_sarif.FindingsError) as e:
        review_sarif.normalise({"findings": [finding]})
    assert needle in str(e.value)


def test_sarif_shape_is_2_1_0():
    sarif = review_sarif.to_sarif(review_sarif.parse_findings(_block(GOOD)),
                                  ticket_id=5145, repo="/home/lumen/foreman")
    assert sarif["version"] == "2.1.0"
    assert sarif["$schema"].endswith("sarif-schema-2.1.0.json")
    run = sarif["runs"][0]
    rules = run["tool"]["driver"]["rules"]
    assert [r["id"] for r in rules] == ["py-shell-injection", "review-finding"]
    res = run["results"]
    assert len(res) == 3
    assert res[0]["level"] == "error" and res[0]["ruleIndex"] == 0
    loc = res[0]["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"] == {"uri": "harness/x.py", "uriBaseId": "SRCROOT"}
    assert loc["region"] == {"startLine": 12, "endLine": 14}
    assert "region" not in res[2]["locations"][0]["physicalLocation"]
    assert run["originalUriBaseIds"]["SRCROOT"]["uri"] == "file:///home/lumen/foreman/"
    assert run["properties"]["ticket"] == "wq#5145"
    for r in res:
        assert r["level"] in review_sarif.LEVELS


def test_clean_review_is_valid():
    sarif = review_sarif.to_sarif(review_sarif.parse_findings(_block({"findings": []})))
    assert sarif["runs"][0]["results"] == []


def test_emit_writes_private_files(tmp_path):
    out = review_sarif.emit(_block(GOOD), str(tmp_path), ticket_id=5145)
    assert out["count"] == 3 and out["levels"] == {"error": 1, "warning": 1, "note": 1}
    for p in (out["sarif"], out["json"]):
        assert os.stat(p).st_mode & 0o777 == 0o600
    with open(out["sarif"]) as f:
        assert json.load(f)["version"] == "2.1.0"


def test_cli_exit_codes(tmp_path):
    good = tmp_path / "good.txt"
    good.write_text(_block(GOOD))
    bad = tmp_path / "bad.txt"
    bad.write_text("no findings")
    assert review_sarif.main([str(good), str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / review_sarif.SARIF_FILENAME).is_file()
    assert review_sarif.main([str(bad), str(tmp_path / "out2")]) == 2
    assert not (tmp_path / "out2").exists()


# ---- fworker close path ------------------------------------------------------------

def _fworker(tmp_path, monkeypatch):
    import fworker
    importlib.reload(fworker)
    fworker.RUNS_ROOT = str(tmp_path)
    calls = []
    monkeypatch.setattr(fworker, "wq_done",
                        lambda tid, notes, **kw: calls.append((tid, notes, kw)))
    return fworker, calls


def test_close_review_readonly_writes_sarif_and_closes(tmp_path, monkeypatch):
    fworker, calls = _fworker(tmp_path, monkeypatch)
    out = fworker.close_review_readonly(5145, _block(GOOD), repo="/home/lumen/foreman")
    assert out and os.path.isfile(out["sarif"])
    assert out["sarif"] == os.path.join(str(tmp_path), "wq5145", "review.sarif")
    (tid, notes, kw), = calls
    assert tid == 5145 and out["sarif"] in notes and "3 findings" in notes
    assert kw["unlanded_reason"] and kw["no_artifact_reason"]


def test_close_review_readonly_leaves_ticket_open_on_bad_block(tmp_path, monkeypatch):
    fworker, calls = _fworker(tmp_path, monkeypatch)
    assert fworker.close_review_readonly(5145, "I reviewed it, looks fine.") is None
    assert calls == []
    assert not os.path.exists(os.path.join(str(tmp_path), "wq5145", "review.sarif"))


def test_close_review_readonly_survives_refused_close(tmp_path, monkeypatch):
    fworker, _ = _fworker(tmp_path, monkeypatch)

    def refuse(*a, **kw):
        raise RuntimeError("wq done refused")
    monkeypatch.setattr(fworker, "wq_done", refuse)
    assert fworker.close_review_readonly(5145, _block(GOOD)) is None
