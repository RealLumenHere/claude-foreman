import os

import pytest
import yaml

from fakes import FakeNode, FakeOllama
from mb_matrix import load_matrix
from mb_node import Refused, screen
from mb_render import render
from mb_runner import Results, Runner, plan_rows

MATRIX = {
    "defaults": {"ollama": {"threads": 12, "ctx": 32768, "thinking": "off"}},
    "rows": [
        {"id": "a", "model": "m", "quant": "Q4_K_M", "engine": "ollama", "tag": "m:a"},
        {"id": "b", "model": "m", "quant": "Q4_K_M", "engine": "ollama", "tag": "m:b",
         "variants": {"Q8_0": "m:b-q8"}},
        {"id": "sweep", "sweep": {"of": ["a", "b"], "quants": ["Q8_0", "Q4_0"]}},
    ],
}


@pytest.fixture
def matrix(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump(MATRIX))
    return load_matrix(str(p))


def make(tmp_path, matrix, node=None, http=None, **kw):
    node = node or FakeNode()
    results = Results(str(tmp_path / "r.jsonl"))
    return Runner(node, matrix, results, http=http or FakeOllama(tags=["m:a", "m:b"]),
                  sampler_interval=0.05, log=lambda *_: None, **kw), results, node


def phases(results, row):
    return [r["phase"] for r in results.records() if r.get("row") == row]


def test_full_row_emits_every_measurement(tmp_path, matrix):
    runner, results, node = make(tmp_path, matrix, only={"a"})
    runner.run()
    got = phases(results, "a")
    for want in ("row_start", "warm", "prefill_512", "prefill_4096", "prefix_reuse_4096",
                 "prefill_8192", "decode", "prefill_solo_conc", "concurrent_x2", "row_done"):
        assert want in got, want
    by = {r["phase"]: r for r in results.records() if r.get("row") == "a"}
    assert by["prefill_512"]["prompt_tps"] == 20.0            # from the server's own counters
    assert by["prefix_reuse_4096"]["reuse_frac"] > 0.9
    assert by["prefill_4096"]["pkg_c_max"] == 61 and by["prefill_4096"]["fan_rpm_max"] == 1800
    assert by["decode"]["resident_bytes"] == 9 * 10**9
    assert 3800 < by["prefill_4096"]["prompt_new_n"] < 4400    # nonce'd prompt sized to target


def test_watts_recorded_when_node_has_plug(tmp_path, matrix):
    runner, results, _ = make(tmp_path, matrix, node=FakeNode(plug="p3"), only={"a"})
    runner.run()
    rec = next(r for r in results.records() if r.get("phase") == "prefill_512")
    assert rec["watts_mean"] == 88.0


def test_resume_skips_finished_rows(tmp_path, matrix):
    runner, results, _ = make(tmp_path, matrix, only={"a"})
    runner.run()
    http2 = FakeOllama(tags=["m:a", "m:b"])
    runner2, _, _ = make(tmp_path, matrix, http=http2, only={"a"})
    runner2.run()
    assert not [c for c in http2.calls if c[0] == "/api/chat"]


def test_edited_row_reruns(tmp_path, matrix):
    runner, results, _ = make(tmp_path, matrix, only={"a"})
    runner.run()
    matrix.rows()[0].flags["threads"] = 20
    http2 = FakeOllama(tags=["m:a"])
    runner2, _, _ = make(tmp_path, matrix, http=http2, only={"a"})
    runner2.run()
    assert [c for c in http2.calls if c[0] == "/api/chat"]


def test_restores_prior_resident_and_unloads_ours(tmp_path, matrix):
    http = FakeOllama(tags=["m:a", "m:b"], resident=["keep:1"])
    runner, results, _ = make(tmp_path, matrix, http=http, only={"a"})
    runner.run()
    assert list(http.loaded) == ["keep:1"]                    # ours gone, theirs back
    order = [(c[0], c[1].get("model"), c[1].get("keep_alive")) for c in http.calls
             if c[0] == "/api/generate"]
    assert order[0] == ("/api/generate", "keep:1", 0)          # unloaded before the row
    assert order[-1][1] == "keep:1" and order[-1][2] != 0      # restored at the end


def test_restores_even_when_a_row_fails(tmp_path, matrix):
    class Boom(FakeOllama):
        def post(self, url, body, timeout=0):
            if url.endswith("/api/chat"):
                raise RuntimeError("engine died")
            return super().post(url, body, timeout)

    http = Boom(tags=["m:a"], resident=["keep:1"])
    runner, results, _ = make(tmp_path, matrix, http=http, only={"a"})
    runner.run()
    assert "row_error" in phases(results, "a") and "row_done" not in phases(results, "a")
    assert list(http.loaded) == ["keep:1"]


def test_missing_model_is_pulled_after_disk_check(tmp_path, matrix):
    http = FakeOllama(tags=[])
    runner, results, node = make(tmp_path, matrix, http=http, only={"a"})
    runner.run()
    assert ("/api/pull", {"model": "m:a", "stream": False}) in http.calls
    assert any(c.startswith("df ") for c in node.commands)


def test_refuses_under_30gb_free(tmp_path, matrix):
    runner, results, _ = make(tmp_path, matrix, node=FakeNode(free_gb=29.9), only={"a"})
    with pytest.raises(Refused, match="30 GB"):
        runner.run()
    assert results.records() == []


def test_refuses_when_model_would_leave_under_30gb(tmp_path, matrix):
    matrix.rows()[0].size_gb = 20
    runner, results, _ = make(tmp_path, matrix, node=FakeNode(free_gb=40),
                              http=FakeOllama(tags=[]), only={"a"})
    with pytest.raises(Refused, match="30 GB"):
        runner.run()
    assert "row_done" not in phases(results, "a")


def test_serving_group_node_refused_without_flag(tmp_path, matrix):
    node = FakeNode("hathor", "100.80.71.13")
    runner, results, _ = make(tmp_path, matrix, node=node)
    with pytest.raises(Refused, match="local-worker group"):
        runner.run()
    assert results.records() == []
    allowed, results2, _ = make(tmp_path, matrix, node=node, allow_serving_group=True,
                                only={"a"})
    allowed.run()
    assert "row_done" in phases(results2, "a")


def test_thor_never(tmp_path, matrix):
    runner, _, _ = make(tmp_path, matrix, node=FakeNode("thor", "100.112.151.100"))
    with pytest.raises(Refused, match="thor"):
        runner.run()


def test_sweep_expands_to_best_candidate_variants(tmp_path, matrix):
    runner, results, _ = make(tmp_path, matrix)
    plan = plan_rows(matrix, results)
    assert "pending" in plan[-1][1]
    results.write({"row": "b", "phase": "decode", "decode_tps": 9.0})
    results.write({"row": "a", "phase": "decode", "decode_tps": 3.0})
    plan = plan_rows(matrix, results)
    ids = [getattr(i, "id") for i, _ in plan]
    assert "sweep-q8_0" in ids                                # b has a Q8_0 variant
    skipped = [i for i, _ in plan if getattr(i, "why", None)]
    assert skipped and "variants.Q4_0" in skipped[0].why       # b has no Q4_0: skipped, not guessed
    row = next(i for i, _ in plan if getattr(i, "id", "") == "sweep-q8_0")
    assert row.tag == "m:b-q8" and row.quant == "Q8_0"


def test_think_flag_sent_then_dropped_when_model_rejects_it(tmp_path, matrix):
    import io
    import urllib.error

    class NoThink(FakeOllama):
        def post(self, url, body, timeout=0):
            if url.endswith("/api/chat") and "think" in body:
                self.calls.append(("/api/chat", dict(body)))
                raise urllib.error.HTTPError(url, 400, "does not support thinking", {}, io.BytesIO())
            return super().post(url, body, timeout)

    http = NoThink(tags=["m:a"])
    runner, results, _ = make(tmp_path, matrix, http=http, only={"a"})
    runner.run()
    chats = [c[1] for c in http.calls if c[0] == "/api/chat"]
    assert chats[0].get("think") is False                     # thinking: off was requested
    assert all("think" not in c for c in chats[1:])            # then never sent again
    assert "row_done" in phases(results, "a")


def test_no_command_can_power_anything():
    for bad in ("sudo reboot", "shutdown -h now", "systemctl suspend", "systemctl stop x",
                "poweroff", "echo a; halt", "ip link set eth0 down"):
        with pytest.raises(Refused):
            screen(bad)
    screen("systemctl list-units 'llama*' --state=active --no-legend")
    screen("kill 1234")


def test_all_node_commands_pass_the_deny_list(tmp_path, matrix):
    runner, results, node = make(tmp_path, matrix, only={"a"})
    runner.run()
    for c in node.commands:
        screen(c)


def test_render_table(tmp_path, matrix):
    runner, results, _ = make(tmp_path, matrix, only={"a"})
    runner.run()
    md = render(results.records(), "wadjet")
    line = next(ln for ln in md.splitlines() if ln.startswith("| a |"))
    assert "20.0" in line and "| 5.0 |" in line and "9.0" in line
    assert sum(ln.startswith("|---") for ln in md.splitlines()) == 1


def test_torn_last_line_is_ignored(tmp_path, matrix):
    runner, results, _ = make(tmp_path, matrix, only={"a"})
    runner.run()
    with open(results.path, "a") as fh:
        fh.write('{"row": "a", "phase": "row_do')
    assert results.done()["a"] == matrix.rows()[0].digest()
    assert os.path.getsize(results.path) > 0
