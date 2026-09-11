"""wq#1729: pin the reject decision as a regression guard, not just a doc."""
import importlib.util
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.dirname(TESTS_DIR)

spec = importlib.util.spec_from_file_location(
    "guard_no_fabricated_kv_flags",
    os.path.join(MODULE_DIR, "guard_no_fabricated_kv_flags.py"))
guard = importlib.util.module_from_spec(spec)
sys.modules["guard_no_fabricated_kv_flags"] = guard
spec.loader.exec_module(guard)


def test_no_fabricated_kv_cache_compression_flag_in_repo():
    assert guard.find_fabricated_kv_flags(guard.REPO_ROOT) == []


def test_detects_fabricated_flag_when_present(tmp_path):
    ops_dir = tmp_path / "ops" / "wq0000-fixture"
    ops_dir.mkdir(parents=True)
    snippet = ops_dir / "litellm_config_snippet.yaml"
    snippet.write_text(
        "model_list:\n"
        "  - model_name: fixture-local\n"
        "    litellm_params:\n"
        "      kv_cache_compression: true\n"
    )
    hits = guard.find_fabricated_kv_flags(tmp_path)
    assert hits == [(str(snippet.relative_to(tmp_path)), "kv_cache_compression")]
