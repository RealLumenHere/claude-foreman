#!/usr/bin/env python3
"""wq#2019 — regression tests for minerva_dedup.route_conflict().

Before the fix, route_conflict() flagged a "duplicate route" on ANY proposal
whose text merely mentioned an existing route's model_name near a generic
litellm/gateway keyword - rejecting real technique proposals (caching, quality
routing, memory orchestration) that use an existing model rather than propose
re-adding it. Cases below are lightly paraphrased from the wq#2019 report's
cited false-positive reject reasons (glm-5.3-flash-local, qwen36-local, bge-m3,
claude-max-sonnet all-already-exists) and the module's own genuine-duplicate
example from its docstring/log ("add model X to LiteLLM").
"""
import sys
import tempfile
import yaml

import minerva_dedup

ROUTES = ['glm-5.3-flash-local', 'qwen36-local', 'bge-m3', 'claude-max-sonnet']


def _with_config(names):
    cfg = {'model_list': [{'model_name': n} for n in names]}
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)
    yaml.safe_dump(cfg, f)
    f.close()
    return f.name


def run():
    minerva_dedup.LITELLM_CONFIG = _with_config(ROUTES)
    failures = []

    # False positives (wq#2019): technique proposals that USE an existing
    # route's model, not propose adding it, must no longer conflict.
    false_positive_cases = [
        "Add prompt caching in front of glm-5.3-flash-local to cut repeated-prefix "
        "spend on the LiteLLM gateway.",
        "All Lumen inference goes through the LiteLLM gateway. Route judge calls "
        "through qwen36-local for quality-based model selection before falling "
        "back to a paid rung on the same gateway route.",
        "Use bge-m3 embeddings for memory orchestration; the embedding call goes "
        "through the same LiteLLM model pool as every other local model.",
        "Evaluate claude-max-sonnet output quality against the local judge rung "
        "for agreement scoring, sampled from the LiteLLM gateway route logs.",
    ]
    for text in false_positive_cases:
        conflict, route = minerva_dedup.route_conflict(text)
        if conflict:
            failures.append(f'FALSE POSITIVE still triggers: route={route!r} text={text[:60]!r}')

    # True positive: an actual proposal to add a route whose name already
    # exists must still be caught.
    true_positive_cases = [
        'Add model glm-5.3-flash-local as a new LiteLLM route for local judge traffic.',
        'Register a new litellm route named qwen36-local pointing at the Isis llama-server.',
    ]
    for text in true_positive_cases:
        conflict, route = minerva_dedup.route_conflict(text)
        if not conflict:
            failures.append(f'TRUE POSITIVE missed: text={text[:60]!r}')

    if failures:
        print('FAIL:')
        for f in failures:
            print(' -', f)
        return 1
    print(f'OK: {len(false_positive_cases)} false positives cleared, '
          f'{len(true_positive_cases)} true positives still caught')
    return 0


if __name__ == '__main__':
    sys.exit(run())
