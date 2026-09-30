"""wq#3212 — removal surgery + shared rung-list module.

The dead-rung fixture below mirrors the live litellm_config.yaml shapes the
ticket names (anthropic provider-prefix rung, :8091 shim rungs, :8000 local
rungs) plus survivors with comments, so every gate here can actually fail:
comment preservation, parsed-equality, the outside-model_list reference
refusal, and the emptied-host derivation.
"""
import pathlib
import sys

import yaml

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import litellm_rungs  # noqa: E402
import remove_dead_rungs as rdr  # noqa: E402

FIXTURE = """\
model_list:
  # cerebras: primary cloud rung — MUST SURVIVE, comment included
  - model_name: cerebras-llama
    litellm_params:
      model: cerebras/llama3.1-8b
      api_base: "https://api.cerebras.ai"
      input_cost_per_token: 0.0000001
  # (a) anthropic key defused by design
  - model_name: claude-opus-5
    litellm_params:
      model: anthropic/claude-opus-5
  # (b) claude-shim never installed
  - model_name: claude-max-sonnet
    litellm_params:
      model: openai/claude-max-sonnet
      api_base: "http://host.docker.internal:8091"
  - model_name: claude-max-opus
    litellm_params:
      model: openai/claude-max-opus
      api_base: "http://host.docker.internal:8091"
  # (c) docker-proxy to an unrelated 127.0.0.1 bind
  - model_name: lumen-lfm2.5-2.6b
    litellm_params:
      model: openai/lfm2.5-2.6b
      api_base: "http://host.docker.internal:8000"
  - model_name: kimik3-0.4b-local
    litellm_params:
      model: openai/kimik3
      api_base: "http://host.docker.internal:8000"
  - model_name: messenger-chat
    litellm_params:
      model: anthropic/claude-sonnet
litellm_settings:
  num_retries: 2
"""


def _write(tmp_path, text=FIXTURE):
    p = tmp_path / "litellm_config.yaml"
    p.write_text(text)
    return p


def test_check_reports_and_never_writes(tmp_path, capsys):
    p = _write(tmp_path)
    assert rdr.main(["--check", "--config", str(p)]) == 0
    assert p.read_text() == FIXTURE
    out = capsys.readouterr().out
    assert "check PASS" in out


def test_apply_removes_exactly_the_five_and_keeps_comments(tmp_path, monkeypatch):
    resolved = []
    monkeypatch.setattr(rdr, "resolve_aq_rows", lambda keys: resolved.extend(keys))
    p = _write(tmp_path)
    assert rdr.main(["--apply", "--config", str(p)]) == 0

    text = p.read_text()
    cfg = yaml.safe_load(text)
    names = [e["model_name"] for e in cfg["model_list"]]
    assert names == ["cerebras-llama", "messenger-chat"]
    assert cfg["litellm_settings"] == {"num_retries": 2}
    # survivor comment and pricing key intact (wq#1617 pricing-erasure concern)
    assert "MUST SURVIVE" in text
    assert cfg["model_list"][0]["litellm_params"]["input_cost_per_token"] == 0.0000001
    # removed rungs' lead comments went with them
    assert "claude-shim never installed" not in text
    # backup written next to the config
    assert list(tmp_path.glob("litellm_config.yaml.bak.*_wq3212"))
    # every host the removal emptied of PROBEABLE rungs gets its
    # litellm-completion aq row resolved. messenger-chat still lives on the
    # anthropic host but is NEVER_PROBE (bug #22249), so that host is emptied
    # too — exactly the aq 2579/2583/2585 key set.
    assert sorted(resolved) == ["anthropic", "host.docker.internal:8000",
                                "host.docker.internal:8091"]


def test_apply_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(rdr, "resolve_aq_rows", lambda keys: None)
    p = _write(tmp_path)
    assert rdr.main(["--apply", "--config", str(p)]) == 0
    after_first = p.read_text()
    assert rdr.main(["--apply", "--config", str(p)]) == 0
    assert p.read_text() == after_first


def test_refuses_when_fallback_chain_references_a_removed_name(tmp_path):
    """The gate that can fail: a router fallback naming a removed rung would
    break the gateway at recreate — the script must refuse to write."""
    p = _write(tmp_path, FIXTURE + """\
router_settings:
  fallbacks:
    - cerebras-llama:
        - claude-max-opus
""")
    before = p.read_text()
    assert rdr.main(["--apply", "--config", str(p)]) == 1
    assert p.read_text() == before
    assert not list(tmp_path.glob("*.bak.*"))


def test_rungs_module_on_committed_wq2903_fixture():
    """Pin the shared enumeration to a fixture committed by ANOTHER ticket
    (ops/wq2903), not one authored alongside this code (wq#2630 discipline)."""
    fixture = HERE.parent.parent / "wq2903-model-ref-drift" / "tests" / "fixtures" / "litellm_config.yaml"
    groups = litellm_rungs.host_groups(str(fixture))
    # no api_base anywhere in that fixture -> hosts are the provider prefixes,
    # with both deepseek rungs grouped under one host
    assert dict(groups) == {"cerebras": ["cerebras-llama"],
                            "deepseek": ["deepseek-v4-flash", "deepseek-v4-pro"],
                            "ollama_chat": ["hathor-llama8b"]}
    rungs = litellm_rungs.one_rung_per_host(str(fixture))
    assert [(r.host, r.model_name) for r in rungs] == [
        ("cerebras", "cerebras-llama"), ("deepseek", "deepseek-v4-flash"),
        ("ollama_chat", "hathor-llama8b")]
