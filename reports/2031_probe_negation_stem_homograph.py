#!/usr/bin/env python3
"""wq#2031 gate probe: every bare "<stem>nt" spelling _NEGATION_RE accepts
without an apostrophe must NOT be an ordinary English word.

wq#2025 (PR#646) enumerated bare stems on the stated grounds that their
spelling is "unambiguous", but never checked the claim; ca+nt="cant" and
wo+nt="wont" are both dictionary words, and each silently suppressed a genuine
duplicate route add (wq#2028 repro, 2028_probe_negation_stem_homograph.py).
That is a false NEGATIVE on the gate's core job, the failure direction the
module's own comment calls worse than the false positive it was fixing.

Two checks, run against the shipped module:
  1. dictionary - each accepted bare spelling is absent from the system word
     list (skipped, loudly, if the host has no word list).
  2. behaviour  - each accepted bare spelling, used as a decoy word inside the
     add-intent clause of a real route-add proposal, suppresses that proposal;
     and the spellings wq#2031 dropped no longer do.
Check 2 is what makes check 1 matter: a spelling that suppresses AND is a real
word is a live false negative. Run before adding any stem to _NEGATION_RE.

Run from repo root: python3 reports/2031_probe_negation_stem_homograph.py
"""
import os
import re
import sys
import tempfile

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                                'ops', 'wq2019-proposal-reviewer-fix'))
import minerva_dedup  # noqa: E402

ROUTES = ['glm-5.3-flash-local', 'qwen36-local', 'bge-m3', 'claude-max-sonnet']
WORDLIST = '/usr/share/dict/words'
# The apostrophe-less alternatives, read back off the compiled pattern rather
# than retyped here, so this probe cannot drift out of sync with the regex it
# guards.
_BARE_ARM_RE = re.compile(r"\(\?:((?:[a-z]+\|)+[a-z]+)\)nt")


def bare_spellings():
    arm = _BARE_ARM_RE.search(minerva_dedup._NEGATION_RE.pattern)
    if not arm:
        raise SystemExit('probe stale: no bare "(?:a|b|...)nt" arm in _NEGATION_RE')
    return [s + 'nt' for s in arm.group(1).split('|')]


def _config(names):
    cfg = {'model_list': [{'model_name': n} for n in names]}
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)
    yaml.safe_dump(cfg, f)
    f.close()
    return f.name


def _proposal(word):
    """A genuine duplicate route add whose clause carries `word` as a decoy."""
    return (f'Add a {word}-adjusted route named bge-m3 to the litellm model '
            f'pool as a new entry.')


def run():
    minerva_dedup.LITELLM_CONFIG = _config(ROUTES)
    spellings = bare_spellings()
    failures = []

    if os.path.exists(WORDLIST):
        with open(WORDLIST, encoding='utf-8', errors='ignore') as fh:
            words = {w.strip().lower() for w in fh}
        for s in spellings:
            if s in words:
                failures.append(
                    f'HOMOGRAPH: bare {s!r} is an ordinary English word, so an '
                    f'add-intent clause containing it is read as negated')
        dict_note = f'{len(spellings)} bare spellings vs {len(words)} dictionary words'
    else:
        dict_note = f'{WORDLIST} absent - dictionary check SKIPPED'

    # Each accepted bare spelling really does suppress: that is why a
    # homograph among them is a false negative and not a harmless entry.
    for s in spellings:
        conflict, _ = minerva_dedup.route_conflict(_proposal(s))
        if conflict:
            failures.append(
                f'INERT: bare {s!r} is listed in _NEGATION_RE but does not '
                f'suppress - the dictionary check above is guarding nothing')

    # The two wq#2031 dropped, and the apostrophe'd forms that replace them.
    for word in ('cant', 'wont'):
        conflict, route = minerva_dedup.route_conflict(_proposal(word))
        if not conflict or route != 'bge-m3':
            failures.append(
                f'REGRESSION: {word!r} decoy still suppresses a real route add '
                f'-> {(conflict, route)!r}')
    for neg in ("can't", "won't", 'cannot', 'can not'):
        text = f'Wrap the gateway in a cache; this {neg} add a new litellm route for bge-m3.'
        conflict, route = minerva_dedup.route_conflict(text)
        if conflict:
            failures.append(
                f'NEGATION LOST: {neg!r} no longer suppresses -> route={route!r}')

    if failures:
        print('FAIL:')
        for f in failures:
            print(' -', f)
        return 1
    print(f'OK: {dict_note}; all {len(spellings)} bare spellings suppress and '
          f'none is a dictionary word; cant/wont decoys caught as bge-m3 '
          f"duplicates; can't/won't/cannot/can not still suppress")
    return 0


if __name__ == '__main__':
    sys.exit(run())
