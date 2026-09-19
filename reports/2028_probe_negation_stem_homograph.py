"""wq#2028 verify-pass probe: PR#646's bare-"nt" auxiliary stems collide with
real English words, silently dropping genuine route-add duplicates.
Run from repo root: python3 reports/2028_probe_negation_stem_homograph.py
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

# Each of these is a genuine route-add proposal for a route name that already
# exists (bge-m3) - route_conflict() must return (True, 'bge-m3'). The verb,
# an ordinary English word that happens to collide with one of _NEGATION_RE's
# bare "<stem>nt" alternatives (ca+nt="cant", wo+nt="wont"), and the route
# name all sit in the same clause, so the collision suppresses a real conflict.
cases = [
    ("cant", "Add a cant-adjusted route named bge-m3 to the litellm model pool as a new entry."),
    ("wont", "Register the wont pattern as a new litellm route named bge-m3 in the model pool."),
]
for stem, t in cases:
    print(stem, '->', minerva_dedup.route_conflict(t), '|', t)
