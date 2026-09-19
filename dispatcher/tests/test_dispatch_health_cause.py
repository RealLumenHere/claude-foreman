"""wq#1694: the DISPATCHER STALLED page names the cause, not the symptom.

Replays the 2026-09-10 shape: every candidate skipped governor_deny, spawned=0,
and the Claude session is logged out. The page must carry the governor's reason
string and the credential cause; with the credential alarm already open it must
attach to that row instead of paging again. Fake governor + fake credential module.
"""
import json
import os
import sys
import textwrap

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import dispatch_health as dh  # noqa: E402


def _events():
    pid = "fdisp-20260910T081000Z-1"
    evs = [{"pass_id": pid, "event": "pass_start", "ts": "2026-09-10T08:10:00+00:00"}]
    for t in range(5):
        evs.append({"pass_id": pid, "event": "skip", "ticket": 100 + t, "lane": "build",
                    "stage": "budget", "reason": "governor_deny", "model": "claude-opus-5"})
    evs.append({"pass_id": pid, "event": "skip", "ticket": 200, "lane": "x",
                "stage": "lane", "reason": "lane_unmapped"})
    evs.append({"pass_id": pid, "event": "pass_end", "ts": "2026-09-10T08:10:05+00:00",
                "spawned": 0, "candidates": 57, "dispatchable": 57})
    return evs


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    gov = tmp_path / "gov.py"
    gov.write_text(textwrap.dedent("""
        import json, sys
        print(json.dumps({"allow": False, "local_only": True,
                          "reasons": ["governor state stale (usage_poll_429)"]}))
        sys.exit(1)
    """))
    cred = tmp_path / "cred"
    cred.mkdir()
    (cred / "credential_expiry.py").write_text(textwrap.dedent(f"""
        import json, os
        FIRE = {str(tmp_path / 'fire')!r}
        ATTACHED = {str(tmp_path / 'attached')!r}
        def cause_line():
            return "CAUSE: claude-oauth DEAD: credentials file blanked (logged out). FIX: ssh lumen@100.117.66.102 'claude auth login'"
        def attach_symptom(symptom, detail):
            if not os.path.exists(FIRE):
                return False
            open(ATTACHED, "a").write(symptom + " " + detail + "\\n")
            return True
    """))
    sys.modules.pop("credential_expiry", None)
    monkeypatch.setattr(dh, "GOVERNOR_CLI", str(gov))
    monkeypatch.setattr(dh, "CRED_PROBE_DIR", str(cred))
    log = tmp_path / "dispatcher.log"
    log.write_text("\n".join(json.dumps(e) for e in _events()) + "\n")
    monkeypatch.setattr(dh, "STATE_PATH", str(tmp_path / "health_state.json"))
    monkeypatch.setattr(dh, "_LEGACY_STATE_PATH", str(tmp_path / "legacy.json"))
    sent = []
    monkeypatch.setattr(dh, "_ntfy", lambda msg, **k: sent.append(msg))
    yield tmp_path, log, sent
    sys.modules.pop("credential_expiry", None)


def test_stall_cause_names_governor_reason_and_credential(sandbox):
    cause, cred = dh.stall_cause(_events())
    assert cred is True
    assert cause.startswith("CAUSE: claude-oauth DEAD")
    assert "governor_deny x5" in cause
    assert "governor_deny claude-opus-5: governor state stale (usage_poll_429)" in cause


def test_page_carries_cause_when_no_cause_fire_open(sandbox):
    tmp, log, sent = sandbox
    fired, _ = dh.check_health(str(log), threshold=1, ladder=False)
    assert fired and len(sent) == 1
    assert "CAUSE: claude-oauth DEAD" in sent[0]
    assert "usage_poll_429" in sent[0]


def test_page_attaches_instead_of_paging_when_cause_fire_open(sandbox):
    tmp, log, sent = sandbox
    (tmp / "fire").write_text("1")
    fired, detail = dh.check_health(str(log), threshold=1, ladder=False)
    assert fired and sent == [], "no second urgent page"
    assert "ATTACHED" in detail
    assert "dispatch-stall" in (tmp / "attached").read_text()


def test_cause_lookup_failure_never_blocks_page(sandbox, monkeypatch):
    tmp, log, sent = sandbox
    monkeypatch.setattr(dh, "CRED_PROBE_DIR", str(tmp / "nope"))
    monkeypatch.setattr(dh, "GOVERNOR_CLI", str(tmp / "missing.py"))
    fired, _ = dh.check_health(str(log), threshold=1, ladder=False)
    assert fired and len(sent) == 1
