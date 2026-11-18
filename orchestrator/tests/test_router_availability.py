"""wq#2133 (dispatch rebuild 10/12): routing rows + status/cooldown-aware router.select."""
import copy
import json
import os
import subprocess
import sys
import time

import pytest
import yaml

ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ORCH_DIR)
import router  # noqa: E402

MANIFEST = os.path.join(ORCH_DIR, "..", "research", "models.yaml")
CONFIG = os.path.join(ORCH_DIR, "..", "dispatcher", "config.json")
GROUP = ["hathor-llama8b", "nephthys-qwen14b", "qwen36-local"]


@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    """Cooldown file and dispatcher config both point at scratch copies."""
    monkeypatch.setattr(router.local_capacity, "COOLDOWN_PATH", str(tmp_path / "cooldown.json"))
    monkeypatch.setenv("FOREMAN_BACKEND_COOLDOWN", str(tmp_path / "cooldown.json"))
    monkeypatch.delenv("FOREMAN_LOCAL_CAPACITY", raising=False)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"local_backends": {"local-worker": {"group": GROUP}}}))
    monkeypatch.setattr(router, "DISPATCHER_CONFIG", str(cfg))
    return tmp_path


@pytest.fixture
def raw():
    with open(MANIFEST) as f:
        return yaml.safe_load(f)


@pytest.fixture
def m(raw):
    return router.Manifest(copy.deepcopy(raw))


def cool(tmp_path, *names, until=None):
    until = until if until is not None else time.time() + 900
    (tmp_path / "cooldown.json").write_text(json.dumps({n: until for n in names}))


def pick(m, arch, **kw):
    rung, _ = router.select(m, arch, **kw)
    return rung.spec()


# ---- routing rows ---------------------------------------------------------------------

def test_routing_rows_are_the_step_10_rows(m):
    # wq#2583: local-worker dropped from both chains (NARROW verdict, 0/166 local sessions
    # SUCCESS). standard-engineering is now the only chain still carrying local-worker@local,
    # and only behind local_standard_engineering (false by default).
    assert [r.spec() for r in m.chain("mechanical-edit")] == \
        ["claude-sonnet-5-5@low"]
    assert [r.spec() for r in m.chain("probe-build")] == \
        ["claude-sonnet-5-5@medium", "claude-haiku-5-5"]
    assert [r.spec() for r in m.chain("standard-engineering")] == \
        ["claude-opus-5-5@high", "claude-opus-4-8@high"]   # wq#5083
    assert all(r.model != "qwen36-local" for r in m.chain("mechanical-edit"))
    assert all(r.model != "local-worker" for r in m.chain("mechanical-edit"))
    assert all(r.model != "local-worker" for r in m.chain("probe-build"))


def test_verify_pass_local_author_always_stays_true(m):
    assert m.verify_policy["local_author_always"] is True


def test_route_mechanical_edit_probe_build_no_longer_return_local_worker(m):
    """wq#2583 AC: router route mechanical-edit / probe-build no longer returns local-worker."""
    assert pick(m, "mechanical-edit") == "claude-sonnet-5-5@low"
    assert pick(m, "probe-build") == "claude-sonnet-5-5@medium"


# ---- cooldown (exercised via standard-engineering, the only remaining chain that can
# carry local-worker@local, gated behind local_standard_engineering=True; wq#2583 pulled
# local-worker out of mechanical-edit/probe-build entirely) -----------------------------

def _local_enabled_manifest(raw):
    raw = copy.deepcopy(raw)
    raw["local_standard_engineering"] = True
    return router.Manifest(raw)


def test_every_local_backend_cooling_falls_to_the_cloud_rung(raw, scratch):
    m = _local_enabled_manifest(raw)
    cool(scratch, *GROUP)
    rung, reasons = router.select(m, "standard-engineering")
    assert rung.spec() == "claude-opus-5-5@high"
    assert any("skip local-worker@local" in r and "every local-worker member is out" in r
               for r in reasons)


def test_group_row_in_cooldown_is_out_by_itself(raw, scratch):
    m = _local_enabled_manifest(raw)
    cool(scratch, "local-worker")
    assert pick(m, "standard-engineering") == "claude-opus-5-5@high"


def test_one_live_member_keeps_the_group(raw, scratch):
    m = _local_enabled_manifest(raw)
    cool(scratch, "hathor-llama8b")
    assert pick(m, "standard-engineering") == "local-worker@local"


def test_down_member_counts_as_out_alongside_cooling_ones(raw, scratch):
    # qwen36-local is `down` in models.yaml, so hathor + nephthys cooling empties the group
    m = _local_enabled_manifest(raw)
    cool(scratch, "hathor-llama8b", "nephthys-qwen14b")
    assert pick(m, "standard-engineering") == "claude-opus-5-5@high"


def test_expired_cooldown_is_ignored(raw, scratch):
    m = _local_enabled_manifest(raw)
    cool(scratch, *GROUP, until=time.time() - 5)
    assert pick(m, "standard-engineering") == "local-worker@local"


def test_object_shaped_and_iso_entries_are_read(raw, scratch):
    m = _local_enabled_manifest(raw)
    (scratch / "cooldown.json").write_text(json.dumps(
        {n: {"until": "2999-01-01T00:00:00Z", "why": "hang"} for n in GROUP}))
    assert pick(m, "standard-engineering") == "claude-opus-5-5@high"


def test_unreadable_cooldown_file_is_no_cooldown(raw, scratch):
    m = _local_enabled_manifest(raw)
    (scratch / "cooldown.json").write_text("{not json")
    assert pick(m, "standard-engineering") == "local-worker@local"


def test_fails_walk_position_survives_a_cooled_rung(m):
    """probe-build's base chain is now cloud-only (wq#2583); fails walks it directly."""
    assert pick(m, "probe-build", fails=0) == "claude-sonnet-5-5@medium"
    assert pick(m, "probe-build", fails=1) == "claude-haiku-5-5"


def test_candidates_drops_cooled_rungs_by_default(m, scratch):
    cool(scratch, *GROUP)
    assert [r.spec() for r in router.candidates(m, "probe-build")] == \
        ["claude-sonnet-5-5@medium", "claude-haiku-5-5"]
    assert len(router.candidates(m, "probe-build", available_only=False)) == 2


def test_static_lever_turns_the_gate_off(raw, scratch, monkeypatch):
    m = _local_enabled_manifest(raw)
    cool(scratch, *GROUP)
    monkeypatch.setenv("FOREMAN_LOCAL_CAPACITY", "static")
    assert pick(m, "standard-engineering") == "local-worker@local"


# ---- model status ---------------------------------------------------------------------

def test_non_live_status_skips_the_rung(raw):
    raw = copy.deepcopy(raw)
    raw["local_standard_engineering"] = True
    for row in raw["models"]:
        if row["id"] == "local-worker":
            row["status"] = "down"
    m = router.Manifest(raw)
    rung, reasons = router.select(m, "standard-engineering")
    assert rung.spec() == "claude-opus-5-5@high"
    assert any("local-worker status is 'down', not LIVE*" in r for r in reasons)


def test_live_variants_all_pass(raw):
    raw = copy.deepcopy(raw)
    raw["local_standard_engineering"] = True
    for row in raw["models"]:
        if row["id"] == "local-worker":
            row["status"] = "LIVE-probe"
    assert pick(router.Manifest(raw), "standard-engineering") == "local-worker@local"


def test_cloud_rung_with_dead_status_is_skipped(raw):
    raw = copy.deepcopy(raw)
    for row in raw["models"]:
        if row["id"] == "claude-sonnet-5-5":
            row["status"] = "TOMBSTONE-CONFIRMED"
    m = router.Manifest(raw)
    assert pick(m, "probe-build", allow_local=False) == "claude-haiku-5-5"


def test_cooled_cloud_rung_is_skipped(m, scratch):
    """wq#3354: fleet-selfheal cools a non-local rung whose provider key is dead
    (object-shaped row, source=fleet-selfheal); dispatch must walk past it. Before
    wq#3354 the cooldown file was read for local rungs only, so this picked sonnet."""
    (scratch / "cooldown.json").write_text(json.dumps({"claude-sonnet-5-5": {
        "until": time.time() + 900, "source": "fleet-selfheal", "reason": "401"}}))
    assert pick(m, "probe-build", allow_local=False) == "claude-haiku-5-5"
    cool(scratch, "claude-sonnet-5-5", until=time.time() - 1)
    assert pick(m, "probe-build", allow_local=False) == "claude-sonnet-5-5@medium"


def test_all_rungs_unavailable_returns_none(m, scratch):
    cool(scratch, *GROUP)
    rung, reasons = router.select(m, "mechanical-edit", pressure=lambda x: "deny")
    assert rung is None
    assert reasons[-1] == "every rung denied, dominated or unavailable"


# ---- local_standard_engineering -------------------------------------------------------

def test_standard_engineering_stays_cloud_while_the_flag_is_false(raw, m):
    assert raw["local_standard_engineering"] is False
    assert m.local_standard_engineering is False
    assert pick(m, "standard-engineering") == "claude-opus-5-5@high"


def test_flag_true_puts_the_local_group_in_front(raw, scratch):
    raw = copy.deepcopy(raw)
    raw["local_standard_engineering"] = True
    m = router.Manifest(raw)
    assert pick(m, "standard-engineering") == "local-worker@local"
    cool(scratch, *GROUP)
    assert pick(m, "standard-engineering") == "claude-opus-5-5@high"
    assert pick(m, "rca-investigation") == "claude-opus-5-5@high"


# ---- served ctx truth -----------------------------------------------------------------

def test_served_ctx_rows_match_the_dispatcher_config(raw):
    rows = {r["id"]: r for r in raw["models"]}
    with open(CONFIG) as f:
        backends = json.load(f)["local_backends"]
    for name in ("hathor-llama8b", "nephthys-qwen14b"):
        assert rows[name]["served_ctx"] == backends[name]["served_ctx"] == 32768
    # wq#3721 (PR#1754, 2026-10-12): isis :8095 serves n_ctx=32768 over 2 slots -> 16384 per slot
    assert rows["qwen36-local"]["served_ctx"] == 16384
    assert rows["local-worker"]["served_ctx"] == \
        min(rows[n]["served_ctx"] for n in ("hathor-llama8b", "nephthys-qwen14b"))
    assert rows["local-worker"]["pools"] == ["local"]


# ---- the proof, through the CLI -------------------------------------------------------

def _route(arch, env_extra):
    env = {**os.environ, "FOREMAN_DISPATCHER_CONFIG": router.DISPATCHER_CONFIG, **env_extra}
    out = subprocess.run([sys.executable, os.path.join(ORCH_DIR, "router.py"), "--manifest",
                          MANIFEST, "route", arch], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)["rung"]


def test_cli_proof(scratch):
    """wq#2583 AC: router route mechanical-edit / probe-build no longer returns local-worker."""
    assert _route("mechanical-edit", {})["model"] == "claude-sonnet-5-5"
    assert _route("probe-build", {})["model"] == "claude-sonnet-5-5"
