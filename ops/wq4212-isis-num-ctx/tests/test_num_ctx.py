import importlib.util
import pathlib

HERE = pathlib.Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


set_num_ctx = _load("set_num_ctx")
ctx_probe = _load("ctx_probe")

# /api/show `parameters` text for qwen3.6:35b-a3b on isis, captured 2026-10-18
# before wq#4212 applied (no num_ctx line -> ollama loaded it at 4096).
SHOW_BEFORE = """draft_num_predict              2
min_p                          0
presence_penalty               1.5
repeat_penalty                 1
temperature                    1
top_k                          20
top_p                          0.95"""


def test_parse_parameters_types():
    p = set_num_ctx.parse_parameters(SHOW_BEFORE)
    assert p == {"draft_num_predict": 2, "min_p": 0, "presence_penalty": 1.5,
                 "repeat_penalty": 1, "temperature": 1, "top_k": 20, "top_p": 0.95}


def test_parse_parameters_stop_is_list_and_unquoted():
    p = set_num_ctx.parse_parameters('stop "<|im_end|>"\nstop "<|endoftext|>"\nnum_ctx 16384')
    assert p == {"stop": ["<|im_end|>", "<|endoftext|>"], "num_ctx": 16384}
    assert set_num_ctx.parse_parameters('stop "<|im_end|>"') == {"stop": ["<|im_end|>"]}


def test_build_create_preserves_params_and_sets_ctx():
    before = set_num_ctx.parse_parameters(SHOW_BEFORE)
    body = set_num_ctx.build_create("qwen3.6:35b-a3b", before, 32768)
    assert body["model"] == body["from"] == "qwen3.6:35b-a3b"
    assert body["parameters"]["num_ctx"] == 32768
    assert {k: v for k, v in body["parameters"].items() if k != "num_ctx"} == before
    assert "num_ctx" not in before


def test_probe_prompt_has_at_least_one_token_per_word():
    prompt = ctx_probe.build_prompt(50)
    assert "w0" in prompt and "w49" in prompt and "w50" not in prompt


def test_verdict_fails_on_wq4212_truncation():
    # The live pre-fix observation: 3000 words evaluated as 2050 tokens.
    assert ctx_probe.verdict(3000, 2050) == "FAIL"
    # Post-fix live observation.
    assert ctx_probe.verdict(3000, 13918) == "PASS"
