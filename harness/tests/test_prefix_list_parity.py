"""Design §8 T3 (no sixth copy, the five agree) and T4 (governor-exemption round trip); wq#2250.
The five LOCAL_POOL_MODELS literals are imported for real and compared with the registry's render.
"""
import ast
import copy
import importlib
import os
import re
import subprocess
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HARNESS_DIR)
for p in (os.path.join(REPO, "governor"), HARNESS_DIR, REPO):
    sys.path.insert(0, p)
import concurrency  # noqa: E402
import fworker  # noqa: E402
import governor_core  # noqa: E402
import inference_registry as ir  # noqa: E402
import node_render  # noqa: E402

LITERAL = re.compile(r'^LOCAL_POOL_MODELS\s*=\s*\(\s*["\']', re.M)
GOVERNOR_FILE = "governor/governor_core.py"


@pytest.fixture(scope="module")
def reg():
    return ir.load()


def _site(name):
    return importlib.import_module(name)


def _rendered(reg):
    return tuple(reg["governor_prefixes"])


@pytest.mark.parametrize("module", ["orchestrator.judgment", "despag.admission", "despag.app"])
def test_editable_copy_equals_registry_render(reg, module):
    assert _site(module).LOCAL_POOL_MODELS == _rendered(reg)


def test_fworker_and_governor_copies_equal_registry_render(reg):
    """wq#2256: the governor derives its set from models.yaml; the frozen floor adds nothing today."""
    assert fworker.LOCAL_POOL_MODELS == _rendered(reg)
    assert set(governor_core._registry_local_ids()) == set(concurrency.LOCAL_POOL_MODELS)
    assert set(governor_core.LOCAL_POOL_MODELS) == set(concurrency.LOCAL_POOL_MODELS)
    assert set(governor_core._FROZEN_LOCAL_POOL) <= set(governor_core.LOCAL_POOL_MODELS)


def test_concurrency_derived_set_agrees_without_being_generated(reg):
    """harness/concurrency.py reads models.yaml pools: the render of target 3 fixes it for free."""
    want = set(_rendered(reg)) | {node_render.LOCAL_GROUP}
    assert set(concurrency.LOCAL_POOL_MODELS) == want
    assert not LITERAL.search(open(os.path.join(HARNESS_DIR, "concurrency.py")).read())


def test_four_editable_literals_plus_one_derived_governor_site():
    tracked = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO, capture_output=True, text=True,
                             check=True).stdout.split()
    sites = sorted(f for f in tracked
                   if "/tests/" not in f and not os.path.basename(f).startswith("test_")
                   and LITERAL.search(open(os.path.join(REPO, f)).read()))
    assert sites == sorted(node_render.PREFIX_FILES), \
        "a sixth LOCAL_POOL_MODELS copy (or a lost one): derive it, do not paste it"
    gov = open(os.path.join(REPO, GOVERNOR_FILE)).read()
    assert re.search(r"^LOCAL_POOL_MODELS = local_pool_models\(\)$", gov, re.M)


def test_prefix_copies_stay_literal_tuples_not_imports():
    for rel in node_render.PREFIX_FILES:
        src = open(os.path.join(REPO, rel)).read()
        assert LITERAL.search(src), rel
        assert "import LOCAL_POOL_MODELS" not in src, rel


def test_governor_is_never_rendered_into():
    assert GOVERNOR_FILE not in node_render.PREFIX_FILES


def _ids(reg):
    ids = ir.model_ids(reg)
    assert ids
    return ids


def test_every_registry_id_is_governor_exempt_everywhere(reg):
    for mid in _ids(reg):
        for probe in (mid, mid + "@low"):
            assert governor_core.is_local_model(probe), probe
            assert concurrency.is_mac_local_model(probe), probe
            assert fworker.is_local_model(probe), probe


def test_node_suffixed_id_stays_exempt_under_the_naming_rule(reg):
    """design §4: `<governor_prefix>.<node>` is exempt by prefix match, effort suffix or not."""
    for prefix in set(_rendered(reg)) - {"deepseek-v4-flash"}:
        for probe in (f"{prefix}.hathor", f"{prefix}.hathor@low"):
            assert governor_core.is_local_model(probe)
            assert fworker.is_local_model(probe)


def test_every_local_rung_model_is_local_model():
    """wq#2565: the router selects rungs from LOCAL_RUNG_MODELS, so every rung id must also
    pass is_local_model — otherwise build_env() takes the cloud OAuth path and the dispatch
    dies on Anthropic's API with model_not_found. local-worker was the silent exception."""
    assert len(set(fworker.LOCAL_RUNG_MODELS)) == len(fworker.LOCAL_RUNG_MODELS)
    for m in fworker.LOCAL_RUNG_MODELS:
        assert fworker.is_local_model(m), m


def test_local_worker_group_alias_is_local_at_every_predicate_site():
    """wq#2565: the group alias the dispatcher emits (--model local-worker) must ride the
    LiteLLM gateway path at every site that branches on it, effort suffix or not."""
    admission = _site("despag.admission")
    app = _site("despag.app")
    for probe in ("local-worker", "local-worker@local"):
        assert fworker.is_local_model(probe), probe
        assert fworker.is_local_rung(probe), probe
        assert governor_core.is_local_model(probe), probe
        assert admission.is_local_model(probe), probe
        assert app._is_local_model(probe), probe


def test_at_sign_as_node_separator_fails_lint_quoting_the_reason(reg):
    bad = copy.deepcopy(reg)
    bad["nodes"]["hathor"]["models"][0]["id"] = "hathor-llama8b@hathor"

    class Inv:
        def plugs(self):
            return {"hathor": "P3", "nephthys": "P2", "isis": "p1"}

        def tailscale_ip(self, name):
            return {n: v["tailscale_ip"] for n, v in bad["nodes"].items()}.get(name)

    found = ir.lint(bad, inventory=Inv(), local_ceiling=12, governor=governor_core.is_local_model)
    assert any(f.level == "error" and "local_capacity.py:51" in f.msg for f in found)


# --- wq#2256: the governor reads the registry --------------------------------------------------
FOURTH = os.path.join(HARNESS_DIR, "tests", "fixtures", "wq2256_models_fourth_family.yaml")
FAMILY = "gemma3-27b"


class _Inv:
    def plugs(self):
        return {"hathor": "P3", "nephthys": "P2", "isis": "p1"}

    def tailscale_ip(self, name):
        return {"hathor": "100.80.71.13", "nephthys": "100.98.74.87", "isis": "100.100.129.24"}.get(name)


def _import_governor(models_yaml):
    """Import governor_core the way foreman_usaged does (bare module, governor/ on sys.path)."""
    code = ("import governor_core as g; print(repr((g.LOCAL_POOL_MODELS, "
            f"g.is_local_model('{FAMILY}.seshat@low'), g.is_local_model('claude-opus-5-5'))))")
    env = dict(os.environ, FOREMAN_MODELS_YAML=models_yaml)
    out = subprocess.run([sys.executable, "-c", code], cwd=os.path.join(REPO, "governor"), env=env,
                         capture_output=True, text=True, check=True).stdout
    return ast.literal_eval(out)


def test_fourth_family_matching_no_frozen_prefix_is_governor_exempt():
    assert not any(FAMILY.startswith(p) for p in governor_core._FROZEN_LOCAL_POOL)
    pool, fourth, claude = _import_governor(FOURTH)
    assert FAMILY in pool and fourth is True and claude is False
    assert set(governor_core._FROZEN_LOCAL_POOL) <= set(pool)


def test_fourth_family_lints_clean_against_its_models_yaml(reg):
    r = copy.deepcopy(reg)
    r["families"][FAMILY] = dict(r["families"]["hathor-llama8b"], blob="gemma3:27b")
    r["governor_prefixes"].append(FAMILY)
    gov = ir.governor_exempt(models_yaml=FOURTH)
    assert gov(FAMILY) and gov(f"{FAMILY}.seshat@low")
    assert [f for f in ir.lint(r, inventory=_Inv(), local_ceiling=12, governor=gov) if f.level == "error"] == []
    # the same registry against today's models.yaml still refuses it: the row is what exempts it
    assert any(FAMILY in f.msg for f in ir.lint(r, inventory=_Inv(), local_ceiling=12,
                                                 governor=governor_core.is_local_model)
               if f.level == "error" and f.rule == "governor-prefix")


def test_claude_and_cloud_rows_stay_non_exempt():
    pool = governor_core.local_pool_models(FOURTH)
    gov = ir.governor_exempt(models_yaml=FOURTH)
    for mid in ("claude-sonnet-5", "claude-opus-5-5", "claude-opus-5-5@high", "mistral-large", "gemini-3-flash"):
        assert mid not in pool, mid
        assert not gov(mid), mid
        assert not governor_core.is_local_model(mid), mid


def test_claude_family_in_the_registry_fails_lint(reg):
    r = copy.deepcopy(reg)
    r["families"]["claude-opus-5-5"] = dict(r["families"]["hathor-llama8b"], blob="x.gguf")
    found = ir.lint(r, inventory=_Inv(), local_ceiling=12, governor=ir.governor_exempt(models_yaml=FOURTH))
    assert any("claude-opus-5-5" in f.msg for f in found if f.level == "error" and f.rule == "governor-prefix")


@pytest.mark.parametrize("content", [None, "models: [\n  - id: [unclosed\n", "just a string\n"])
def test_unreadable_models_yaml_falls_back_to_the_frozen_tuple(tmp_path, content):
    path = str(tmp_path / "models.yaml")
    if content is not None:
        open(path, "w").write(content)
    assert governor_core.local_pool_models(path) == governor_core._FROZEN_LOCAL_POOL
    pool, fourth, claude = _import_governor(path)
    assert pool == governor_core._FROZEN_LOCAL_POOL and fourth is False and claude is False
