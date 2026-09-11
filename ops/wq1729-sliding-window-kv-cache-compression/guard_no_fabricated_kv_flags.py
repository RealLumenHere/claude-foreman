"""Guard against re-landing the fabricated `kv_cache_compression` litellm_params
flag that factory plan c8a0065dd995 staged for wq#1729 (see README.md). Neither
LiteLLM's own config schema nor Ollama's `/api/generate` `options` object
recognizes this key (verified against pilots/2c11123fab8b/REPORT.md:51-64,
same Ollama-backed serving path) — hand-merging that plan into a live
litellm_config.yaml would be a silent no-op with a false paper trail.

Scans every ops/*/litellm_config_snippet.yaml hand-merge fragment (the
established convention for documenting live LiteLLM config changes: see
ops/wq1623-qwen36-queue-guard/litellm_config_snippet.yaml) for known-fabricated
KV-cache keys, so a future rescue-model plan can't reintroduce this one
unnoticed.
"""
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
FABRICATED_KV_CACHE_KEYS = ("kv_cache_compression",)


def find_fabricated_kv_flags(root: pathlib.Path = REPO_ROOT):
    hits = []
    for path in sorted(root.glob("ops/**/litellm_config_snippet.yaml")):
        text = path.read_text()
        for key in FABRICATED_KV_CACHE_KEYS:
            if key in text:
                hits.append((str(path.relative_to(root)), key))
    return hits


if __name__ == "__main__":
    found = find_fabricated_kv_flags()
    if found:
        for rel_path, key in found:
            print(f"{rel_path}: fabricated key {key!r}")
        raise SystemExit(1)
    print("no fabricated KV-cache flags found")
