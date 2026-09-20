import os

import pytest
import yaml

from mb_matrix import MatrixError, load_matrix

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(tmp_path, doc):
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump(doc))
    return load_matrix(str(p))


def test_seed_matrix_is_valid_and_ordered():
    m = load_matrix(os.path.join(HERE, "matrix.yaml"))
    ids = [i.id for i in m.items]
    assert ids[:2] == ["qwen36-35b-a3b-q4km-t12", "qwen36-35b-a3b-q4km-t20"]
    assert ids.index("quant-sweep") < ids.index("llama31-8b-q4km")
    t12, t20 = m.rows()[:2]
    assert (t12.flags["threads"], t20.flags["threads"]) == (12, 20)
    assert t12.engine == "llama-server"


def test_nodes_file_lists_the_macs():
    from mb_node import load_nodes
    assert {"wadjet", "isis", "hathor", "nephthys"} <= set(load_nodes())


def test_bad_rows_rejected(tmp_path):
    base = {"id": "x", "model": "m", "engine": "ollama", "tag": "t"}
    for patch, why in (({"engine": "vllm"}, "engine"),
                       ({"flags": {"nonsense": 1}}, "unknown flags"),
                       ({"flags": {"kv_type": "q4_0"}}, "server-level"),
                       ({"flags": {"ctx": 4096}}, "ctx per slot"),
                       ({"fetch": {"kind": "scp"}}, "fetch.kind"),
                       ({"tag": None}, "tag")):
        with pytest.raises(MatrixError, match=why):
            load(tmp_path, {"defaults": {"ollama": {"ctx": 32768}}, "rows": [{**base, **patch}]})


def test_llama_ctx_is_split_over_slots(tmp_path):
    row = {"id": "x", "model": "m", "engine": "llama-server", "gguf": "m.gguf"}
    with pytest.raises(MatrixError, match="ctx per slot"):
        load(tmp_path, {"rows": [{**row, "flags": {"ctx": 16384, "slots": 4}}]})
    load(tmp_path, {"rows": [{**row, "flags": {"ctx": 65536, "slots": 4}}]})


def test_duplicate_and_unknown_sweep_candidates(tmp_path):
    r = {"id": "a", "model": "m", "engine": "ollama", "tag": "t", "flags": {"ctx": 32768}}
    with pytest.raises(MatrixError, match="duplicate"):
        load(tmp_path, {"rows": [r, r]})
    with pytest.raises(MatrixError, match="unknown candidates"):
        load(tmp_path, {"rows": [r, {"id": "s", "sweep": {"of": ["zz"], "quants": ["Q8_0"]}}]})


def test_digest_changes_with_flags(tmp_path):
    r = {"id": "a", "model": "m", "engine": "llama-server", "gguf": "g",
         "flags": {"ctx": 32768}}
    d1 = load(tmp_path, {"rows": [r]}).rows()[0].digest()
    d2 = load(tmp_path, {"rows": [{**r, "flags": {"ctx": 32768, "threads": 20}}]}).rows()[0].digest()
    assert d1 != d2
