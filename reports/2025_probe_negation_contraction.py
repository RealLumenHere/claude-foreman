"""wq#2025 probe: pick the negation pattern that closes wq#2023's contraction gap.

Run from repo root: python3 reports/2025_probe_negation_contraction.py

wq#2023 showed _NEGATION_RE's enumerated auxiliary list misses shouldn't /
wouldn't / couldn't / can't / mustn't. The fix has to generalize, but the
obvious generalization is a trap, so all three candidates are measured against
the same corpus before one ships:

  OLD      the shipped wq#2022 list.
  NAIVE    \\w+n'?t - apostrophe optional, the way the old list spelled
           doesn'?t / don'?t. Generalized it eats every word ending in "nt"
           ("persistent", "component", "different"), so a real route add
           phrased with one is silently suppressed: a false NEGATIVE, worse
           than the false positive we are fixing.
  CHOSEN   \\w+n't for any contraction (apostrophe required) + the handful of
           auxiliary stems whose bare "nt" spelling is unambiguous, + bare
           "not", which already covers every uncontracted "<aux> not".
"""
import os
import re
import sys
import tempfile

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..',
                                'ops', 'wq2019-proposal-reviewer-fix'))
import minerva_dedup  # noqa: E402

ROUTES = ['glm-5.3-flash-local', 'qwen36-local', 'bge-m3', 'claude-max-sonnet']
cfg = {'model_list': [{'model_name': n} for n in ROUTES]}
f = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)
yaml.safe_dump(cfg, f)
f.close()
minerva_dedup.LITELLM_CONFIG = f.name

OLD = re.compile(
    r"\b(not|no|never|doesn'?t|does not|don'?t|do not|isn'?t|is not|aren'?t|"
    r"won'?t|will not|without|rather than|instead of)\b"
)
NAIVE = re.compile(r"\b(?:\w+n'?t|not|no|never|without|rather than|instead of)\b")
CHOSEN = re.compile(
    r"\b(?:\w+n't|"
    r"(?:do|does|did|is|are|was|were|wo|ca|sha|has|have|had|could|would|"
    r"should|must|might|need|ought|ai)nt|"
    r"not|cannot|no|never|without|rather than|instead of)\b"
)

# Disclaimed adds: route_conflict must return (False, None). One per auxiliary
# wq#2023 reproduced, plus the apostrophe-less and uncontracted spellings.
DISCLAIMED = [
    "Wrap the gateway in a cache; this shouldn't add a new litellm route for bge-m3.",
    "Wrap the gateway in a cache; this wouldn't add a new litellm route for bge-m3.",
    "Wrap the gateway in a cache; this can't add a new litellm route for bge-m3.",
    "Wrap the gateway in a cache; this couldn't add a new litellm route for bge-m3.",
    "Wrap the gateway in a cache; this mustn't add a new litellm route for bge-m3.",
    "Wrap the gateway in a cache; this mightn't add a new litellm route for bge-m3.",
    "Wrap the gateway in a cache; this needn't add a new litellm route for bge-m3.",
    "Wrap the gateway in a cache; this shan't add a new litellm route for bge-m3.",
    "Wrap the gateway in a cache; this cant add a new litellm route for bge-m3.",
    "Wrap the gateway in a cache; this cannot add a new litellm route for bge-m3.",
    "Wrap the gateway in a cache; this should not add a new litellm route for bge-m3.",
    "Wrap the gateway in a cache; this shouldn’t add a new litellm route for bge-m3.",
]

# Real route adds phrased with an ordinary "nt"-final word in the same clause:
# route_conflict must still return True. This is what NAIVE breaks.
GENUINE = [
    "Add a persistent litellm route for bge-m3 so the embedding pool survives restarts.",
    "Register the qwen36-local component as a new litellm route for judge traffic.",
    "Add a different litellm route named claude-max-sonnet for the escalation rung.",
    "Add model glm-5.3-flash-local as a new LiteLLM route for local judge traffic.",
]


def score(name, rx):
    minerva_dedup._NEGATION_RE = rx
    fp = [t for t in DISCLAIMED if minerva_dedup.route_conflict(t)[0]]
    fn = [t for t in GENUINE if not minerva_dedup.route_conflict(t)[0]]
    print(f'{name:7} false positives {len(fp)}/{len(DISCLAIMED)}  '
          f'false negatives {len(fn)}/{len(GENUINE)}')
    for t in fp:
        print(f'          FP {t[:72]!r}')
    for t in fn:
        print(f'          FN {t[:72]!r}')
    return len(fp) + len(fn)


if __name__ == '__main__':
    bad = [score('OLD', OLD), score('NAIVE', NAIVE), score('CHOSEN', CHOSEN)]
    sys.exit(0 if bad[2] == 0 else 1)
