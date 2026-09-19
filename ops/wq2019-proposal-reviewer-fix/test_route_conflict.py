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
        # wq#2022 (wq#2021's live repro): the proposal pre-empts the wq#2019
        # rejection reason in words, and the disclaiming "add" lands within 60
        # chars of both a route keyword and a live model_name.
        "Add semantic caching to the LiteLLM gateway route table; this doesn't "
        "add a new model, just wraps existing route bge-m3 with a cache layer.",
        # Same shape with no punctuation splitting the disclaimer off, so only
        # the negation check clears these three. The last one carries a curly
        # apostrophe, which is what LLM-written proposal text actually emits.
        "Rework the LiteLLM gateway route table so it does not add qwen36-local "
        "a second time.",
        "Ship a cache layer rather than register route claude-max-sonnet again "
        "in litellm.",
        "Wrap the gateway in a cache; this doesn’t add a new litellm route "
        "for bge-m3.",
        # wq#2025: the line above with one modal swapped - each of these hard-
        # rejected under wq#2022's enumerated auxiliary list (wq#2023 repro),
        # including the apostrophe-less and uncontracted spellings.
        "Wrap the gateway in a cache; this shouldn't add a new litellm route "
        "for bge-m3.",
        "Wrap the gateway in a cache; this wouldn't add a new litellm route "
        "for bge-m3.",
        "Wrap the gateway in a cache; this can't add a new litellm route "
        "for bge-m3.",
        "Wrap the gateway in a cache; this couldn't add a new litellm route "
        "for bge-m3.",
        "Wrap the gateway in a cache; this mustn't add a new litellm route "
        "for bge-m3.",
        "Wrap the gateway in a cache; this cant add a new litellm route "
        "for bge-m3.",
        "Wrap the gateway in a cache; this cannot add a new litellm route "
        "for bge-m3.",
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
        # wq#2022: a real route add is still caught when an earlier clause
        # happens to contain a negation - the negation check is clause-scoped,
        # so it must not swallow the add in the clause that follows.
        "This isn't a router rewrite; add model bge-m3 to the LiteLLM "
        "model_list as a new route.",
        # wq#2022: the add is in the second sentence, behind an unrelated first
        # add-intent verb - the rejected "only look at the first verb" fix
        # shape misses this one.
        "Add observability to the fleet dashboard so stalls surface earlier and "
        "cost per turn is visible per lane. Separately, register qwen36-local "
        "as a new litellm route for judge traffic.",
        # wq#2025: a real add whose clause carries an ordinary word ending in
        # "nt". The cheap way to generalize the contraction check - apostrophe
        # optional, as the old doesn'?t / don'?t entries had it - reads these
        # as negations and drops a genuine duplicate on the floor.
        "Add a persistent litellm route for bge-m3 so the embedding pool "
        "survives gateway restarts.",
        "Add a different litellm route named claude-max-sonnet for the "
        "escalation rung.",
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
    # wq#2021 noted the old summary overstated coverage: a case with no
    # add-intent verb returns False before the window logic runs, so it proves
    # nothing about the gate. Count the ones that actually reach it.
    exercised = sum(1 for t in false_positive_cases
                    if minerva_dedup._ADD_INTENT_RE.search(t.lower()))
    print(f'OK: {len(false_positive_cases)} false positives cleared '
          f'({exercised} of them reach the add-intent window logic; the rest '
          f'have no add-intent verb and clear trivially), '
          f'{len(true_positive_cases)} true positives still caught')
    return 0


if __name__ == '__main__':
    sys.exit(run())
