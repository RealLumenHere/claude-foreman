"""inference_registry.py — loader + fleet-node lint. INFERENCE_NODE_REGISTRY.md §8 T1 (wq#2248).
Zero live calls: fleet-inv is a stub object, except one test skipped when the CLI is absent.
"""
import ast
import copy
import os
import subprocess
import sys

import pytest
import yaml

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import inference_registry as ir  # noqa: E402

GOVERNOR = ("deepseek-v4-flash", "qwen36-local", "nephthys-qwen14b", "hathor-llama8b", "local-worker")
PLUGS = {"hathor": "P3", "nephthys": "P2", "isis": "p1"}
IPS = {"hathor": "100.80.71.13", "nephthys": "100.98.74.87", "isis": "100.100.129.24"}


class StubInventory:
    def __init__(self, plugs=None, ips=None):
        self._plugs, self._ips = dict(plugs or PLUGS), dict(ips or IPS)

    def plugs(self):
        return self._plugs

    def tailscale_ip(self, name):
        return self._ips.get(name)


@pytest.fixture
def reg():
    return ir.load()


def run_lint(r, inv=None, ceiling=12):
    return ir.lint(r, inventory=inv or StubInventory(), local_ceiling=ceiling,
                   governor=lambda m: m.startswith(GOVERNOR))


def errors(findings, rule=None):
    return [f for f in findings if f.level == "error" and (rule is None or f.rule == rule)]


def test_real_registry_passes(reg):
    assert errors(run_lint(reg)) == []


def test_real_registry_is_exempt_under_the_repo_governor(reg):
    """wq#2256: the lint asks governor_core.is_local_model itself, loaded by path."""
    gov = ir.governor_exempt()
    assert gov is not None
    assert errors(ir.lint(reg, inventory=StubInventory(), local_ceiling=12, governor=gov)) == []


def test_seeded_rows_reproduce_measured_table(reg):
    got = {}
    for name, node in reg["nodes"].items():
        m, = node["models"]
        got[name] = (m["family"], m["slots"], m["served_ctx"], m["prefill_tps"], m["decode_tps"])
    assert got == {
        "hathor": ("hathor-llama8b", 2, 32768, 13.6, None),
        "nephthys": ("nephthys-qwen14b", 2, 32768, 7.4, 4.79),   # decode measured wq#2499 (db40d77a)
        "isis": ("qwen36-local", 2, 8192, 60, 10.9),
    }
    assert reg["families"]["hathor-llama8b"]["blob"] == "llama3.1:8b"
    assert reg["families"]["nephthys-qwen14b"]["blob"] == "qwen3:14b"
    assert reg["nodes"]["isis"]["state"] == "down"
    assert reg["nodes"]["hathor"]["power_policy"] == "always-on"


def test_unmeasured_stay_null_not_zero(reg):
    assert reg["nodes"]["hathor"]["wake"]["measured_ready_s"] is None
    for node in reg["nodes"].values():
        assert node["models"][0]["load_s"] is None


def test_bad_model_id_names_id_and_governor(reg):
    r = copy.deepcopy(reg)
    r["families"]["gemma3-27b"] = dict(r["families"]["hathor-llama8b"], blob="gemma3:27b")
    errs = errors(run_lint(r), "governor-prefix")
    assert len(errs) == 1
    assert "gemma3-27b" in errs[0].msg and "is_local_model" in errs[0].msg


def test_at_separator_in_explicit_id_rejected(reg):
    r = copy.deepcopy(reg)
    r["nodes"]["hathor"]["models"][0]["id"] = "hathor-llama8b@low"
    assert any("'@'" in f.msg for f in errors(run_lint(r), "governor-prefix"))


def test_dotted_id_under_prefix_accepted(reg):
    r = copy.deepcopy(reg)
    r["nodes"]["hathor"]["models"][0]["id"] = "hathor-llama8b.seshat"
    assert errors(run_lint(r)) == []


def test_two_families_one_prefix_different_blobs(reg):
    r = copy.deepcopy(reg)
    r["families"]["qwen36-local.big"] = dict(r["families"]["qwen36-local"], blob="other.gguf")
    errs = errors(run_lint(r), "shared-prefix")
    assert len(errs) == 1 and "qwen36-local" in errs[0].msg


def test_two_families_one_prefix_same_blob_ok(reg):
    r = copy.deepcopy(reg)
    r["families"]["qwen36-local.b"] = dict(r["families"]["qwen36-local"])
    assert errors(run_lint(r), "shared-prefix") == []


def test_registry_prefix_not_in_governor_fails(reg):
    r = copy.deepcopy(reg)
    r["governor_prefixes"].append("gemma3-27b")
    assert errors(run_lint(r), "governor-prefix")


def test_plug_id_disagrees_with_inventory(reg):
    inv = StubInventory(plugs=dict(PLUGS, nephthys="P9"))
    errs = errors(run_lint(reg, inv), "plug-mismatch")
    assert len(errs) == 1 and "nephthys" in errs[0].msg


def test_node_missing_from_inventory_plugs(reg):
    plugs = {k: v for k, v in PLUGS.items() if k != "isis"}
    assert any("isis" in f.msg for f in errors(run_lint(reg, StubInventory(plugs=plugs)), "plug-mismatch"))


def test_tailscale_ip_disagrees_with_inventory(reg):
    inv = StubInventory(ips=dict(IPS, hathor="100.1.1.1"))
    errs = errors(run_lint(reg, inv), "ip-mismatch")
    assert len(errs) == 1 and "hathor" in errs[0].msg


def test_slots_above_local_ceiling(reg):
    errs = errors(run_lint(reg, ceiling=3), "local-ceiling")
    assert len(errs) == 1 and "4 > local_ceiling 3" in errs[0].msg


def test_down_node_slots_not_counted(reg):
    assert errors(run_lint(reg, ceiling=4), "local-ceiling") == []


def test_duplicate_plug_between_nodes(reg):
    r = copy.deepcopy(reg)
    r["nodes"]["nephthys"]["plug_id"] = "P3"
    assert errors(run_lint(r), "duplicate")


def test_null_rate_ok_zero_rate_rejected(reg):
    r = copy.deepcopy(reg)
    r["nodes"]["hathor"]["models"][0]["decode_tps"] = 0
    assert any("decode_tps" in f.msg for f in errors(run_lint(r), "schema"))


def test_untrusted_capability_needs_reason(reg):
    r = copy.deepcopy(reg)
    del r["nodes"]["isis"]["models"][0]["capabilities"]["standard-engineering"]["reason"]
    assert any("reason" in f.msg for f in errors(run_lint(r), "schema"))


def test_unknown_family_reference(reg):
    r = copy.deepcopy(reg)
    r["nodes"]["hathor"]["models"][0]["family"] = "nope"
    assert any("nope" in f.msg for f in errors(run_lint(r), "schema"))


def test_inventory_unavailable_warns_not_errors(reg):
    class Broken(StubInventory):
        def plugs(self):
            raise OSError("no fleet-inv")

    findings = run_lint(reg, Broken())
    assert errors(findings) == []
    assert any(f.level == "warn" and f.rule == "inventory" for f in findings)


def test_env_override_and_load_errors(tmp_path):
    p = tmp_path / "n.yaml"
    p.write_text("version: 1\nfamilies: {a: {}}\n")
    assert ir.load(str(p))["version"] == 1
    assert ir.local_model_ids(path=str(p)) == ("a",)
    with pytest.raises(ir.RegistryError):
        ir.load(str(tmp_path / "missing.yaml"))


def test_local_model_ids_never_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("a: [unclosed\n")
    assert ir.local_model_ids(("x",), str(bad)) == ("x",)
    assert ir.local_model_ids(("x",), str(tmp_path / "missing.yaml")) == ("x",)


def test_import_never_touches_dispatcher_or_orchestrator():
    with open(os.path.join(HARNESS_DIR, "inference_registry.py")) as f:
        tree = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert not imported & {"dispatcher", "orchestrator"}


def test_import_survives_missing_yaml():
    code = ("import sys; sys.modules['yaml'] = None; sys.path.insert(0, %r); "
            "import inference_registry as ir; print(ir.local_model_ids(('fb',)))" % HARNESS_DIR)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "('fb',)"


@pytest.mark.skipif(not os.path.exists(ir.FLEET_INV), reason="fleet-inv not on this host")
def test_real_registry_matches_live_fleet_inv(reg):
    findings = ir.lint(reg)
    assert errors(findings) == [], [str(f) for f in findings]


def test_cli_lint_exit_codes(tmp_path):
    good = subprocess.run([sys.executable, os.path.join(HARNESS_DIR, "inference_registry.py"), "lint"],
                          capture_output=True, text=True)
    assert good.returncode in (0, 1) and "inference registry lint:" in good.stdout
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"families": {}, "nodes": {}}))
    r = subprocess.run([sys.executable, os.path.join(HARNESS_DIR, "inference_registry.py"),
                        "lint", "--path", str(bad)], capture_output=True, text=True)
    assert r.returncode == 1
