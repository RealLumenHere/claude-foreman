"""Design §8 T3 (no sixth copy, the five agree) and T4 (governor-exemption round trip); wq#2250.
The five LOCAL_POOL_MODELS literals are imported for real and compared with the registry's render.
"""
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
    assert fworker.LOCAL_POOL_MODELS == _rendered(reg)
    assert governor_core.LOCAL_POOL_MODELS == _rendered(reg)


def test_concurrency_derived_set_agrees_without_being_generated(reg):
    """harness/concurrency.py reads models.yaml pools: the render of target 3 fixes it for free."""
    want = set(_rendered(reg)) | {node_render.LOCAL_GROUP}
    assert set(concurrency.LOCAL_POOL_MODELS) == want
    assert not LITERAL.search(open(os.path.join(HARNESS_DIR, "concurrency.py")).read())


def test_exactly_five_files_define_the_literal():
    tracked = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO, capture_output=True, text=True,
                             check=True).stdout.split()
    sites = sorted(f for f in tracked
                   if "/tests/" not in f and not os.path.basename(f).startswith("test_")
                   and LITERAL.search(open(os.path.join(REPO, f)).read()))
    assert sites == sorted((GOVERNOR_FILE,) + node_render.PREFIX_FILES), \
        "a sixth LOCAL_POOL_MODELS copy (or a lost one): derive it, do not paste it"


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

    found = ir.lint(bad, inventory=Inv(), local_ceiling=12, governor=tuple(reg["governor_prefixes"]))
    assert any(f.level == "error" and "local_capacity.py:51" in f.msg for f in found)
