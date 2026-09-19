"""wq#2023 verify-pass probe: modal-contraction negations wq#2022/PR#644 misses.
Run from repo root: python3 reports/2023_probe_negation.py
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..',
                                 'ops', 'wq2019-proposal-reviewer-fix'))
import minerva_dedup, tempfile, yaml

ROUTES = ['glm-5.3-flash-local', 'qwen36-local', 'bge-m3', 'claude-max-sonnet']
cfg = {'model_list': [{'model_name': n} for n in ROUTES]}
f = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)
yaml.safe_dump(cfg, f)
f.close()
minerva_dedup.LITELLM_CONFIG = f.name

cases = [
    "Wrap the gateway in a cache; this shouldn't add a new litellm route for bge-m3.",
    "Wrap the gateway in a cache; this wouldn't add a new litellm route for bge-m3.",
    "Wrap the gateway in a cache; this can't add a new litellm route for bge-m3.",
    "Wrap the gateway in a cache; this couldn't add a new litellm route for bge-m3.",
    "Wrap the gateway in a cache; this mustn't add a new litellm route for bge-m3.",
]
for t in cases:
    print(minerva_dedup.route_conflict(t), '|', t)
