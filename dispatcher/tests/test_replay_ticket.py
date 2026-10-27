"""wq#4686 (design/orchestrator_verify_lane.md S7): foreman_dispatcher.py run
--replay-ticket <id> re-derives a shipped ticket's verify-pass filing with NO spawn
and NO wq write, and writes the result to _runs/replay/<id>.json so T1-T4 can diff
successive router/judgment changes against the same ticket. judgment.file_verify_pass
is stubbed (its own derivation logic is covered by orchestrator/tests/
test_wq4686_verify_derive.py against the real policy files) -- this test only checks
replay_ticket()'s own shaping/no-mutation contract.
"""
import json
import os
import sys

DISPATCHER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(DISPATCHER_DIR)
sys.path.insert(0, DISPATCHER_DIR)
sys.path.insert(0, os.path.join(REPO, "orchestrator"))
sys.path.insert(0, os.path.join(REPO, "harness"))
import foreman_dispatcher  # noqa: E402


class _Manifest:
    verify_policy = {"lane": "build"}


def test_replay_writes_record_with_no_spawn_and_no_wq_write(tmp_path, monkeypatch):
    monkeypatch.setattr(foreman_dispatcher, "RUNS_ROOT", str(tmp_path))
    monkeypatch.setattr(foreman_dispatcher.router, "load_manifest", lambda: _Manifest())
    calls = []

    def _fake_file_verify_pass(ticket_id, *, manifest=None, dry_run=False):
        calls.append((ticket_id, dry_run))
        return {"shipped": ticket_id, "required": True, "filed": None,
                "verify_archetype": "verify-probe",
                "derived_needs": [["ssh", "anubis"]], "block_reasons": []}

    monkeypatch.setattr(foreman_dispatcher.judgment, "file_verify_pass", _fake_file_verify_pass)

    record = foreman_dispatcher.replay_ticket(4614)

    assert calls == [(4614, True)]     # dry_run=True: read-only, never spawns/writes
    assert record["ticket"] == 4614
    assert record["archetype"] == "verify-probe"
    assert record["would_spawn"] is True
    assert record["block_reason"] is None

    out_path = os.path.join(str(tmp_path), "replay", "4614.json")
    assert os.path.isfile(out_path)
    with open(out_path) as f:
        on_disk = json.load(f)
    assert on_disk == record


def test_replay_blocked_ticket_never_would_spawn(tmp_path, monkeypatch):
    # T3 (wq#4614, design S7): a blocked derivation must report would_spawn=False and
    # carry the orchestrator-only block_reason through to the replay record.
    monkeypatch.setattr(foreman_dispatcher, "RUNS_ROOT", str(tmp_path))
    monkeypatch.setattr(foreman_dispatcher.router, "load_manifest", lambda: _Manifest())

    def _fake_file_verify_pass(ticket_id, *, manifest=None, dry_run=False):
        return {"shipped": ticket_id, "required": True, "filed": None,
                "verify_archetype": "verify-pass",
                "derived_needs": [["ssh", "thor"]],
                "block_reasons": ["ssh(thor) refused by policy:never_nodes "
                                  "(fleet_exec_policy.py:221)"]}

    monkeypatch.setattr(foreman_dispatcher.judgment, "file_verify_pass", _fake_file_verify_pass)

    record = foreman_dispatcher.replay_ticket(4614)
    assert record["would_spawn"] is False
    assert record["block_reason"].startswith("orchestrator-only: ssh(thor)")
