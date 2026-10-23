"""wq#4588: context_selector.py must see complete sections while tag_sections.py re-tags.

Each test runs a tagger and the selector against a tmp copy of minerva/context. The tagger
re-tags an unchanged claude_memory.md in a thread with a slow embed (the live embed loop takes
10-40 s); the selector runs repeatedly meanwhile, the way claude_context.sh --core runs it
right after context_builder.py rewrites claude_memory.md and fires minerva-tag-sections.path.

fixtures/tag_sections.orig.py and fixtures/context_selector.py are pinned copies of the live
files as of 2026-10-23. The orig test proves the probe can fail on the mechanism.
"""
import importlib.util
import os
import shutil
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FIX = HERE / 'fixtures'
QUERY = 'session start core context'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ctx(tmp_path):
    shutil.copy(FIX / 'claude_memory.md', tmp_path / 'claude_memory.md')
    (tmp_path / 'LIVE_FLEET_STATE.md').write_text('## LIVE FLEET STATE\n- synthetic live slice\n')
    (tmp_path / 'sections').mkdir()
    return tmp_path


def _tagger(path, ctx, embed_delay):
    ts = _load(f'ts_{path.stem}_{time.monotonic_ns()}', path)
    live_dir = str(ts.SECTIONS_DIR)
    real_path = ts.Path

    def sandboxed_path(p):
        # tag_sections.orig.py builds sec_path from a hardcoded live literal; keep it in tmp.
        return real_path(str(p).replace(live_dir, str(ctx / 'sections')))

    ts.Path = sandboxed_path
    ts.MEM = ctx / 'claude_memory.md'
    ts.OUT_JSON = ctx / 'sections.json'
    ts.SECTIONS_DIR = ctx / 'sections'
    ts.OUT_INDEX = ctx / 'claude_memory_index.md'
    ts.LOG = ctx / 'context.log'

    def slow_embed(_text):
        time.sleep(embed_delay)
        return None

    ts.embed = slow_embed
    return ts


def _selector(ctx):
    cs = _load(f'cs_{time.monotonic_ns()}', FIX / 'context_selector.py')
    cs.SECTIONS_JSON = ctx / 'sections.json'
    cs.SECTIONS_DIR = ctx / 'sections'
    cs.LIVE_STATE = ctx / 'LIVE_FLEET_STATE.md'
    cs.embed = lambda _text: None
    return cs


def _sizes_during_retag(tagger_path, ctx):
    """Seed an at-rest index, then sample select() output sizes while a slow re-tag runs."""
    _tagger(tagger_path, ctx, 0).run()
    cs = _selector(ctx)
    at_rest = len(cs.select(QUERY))
    slow = _tagger(tagger_path, ctx, 0.05)
    t = threading.Thread(target=slow.run)
    t.start()
    sizes, errors = [], []
    while t.is_alive():
        try:
            sizes.append(len(cs.select(QUERY)))
        except Exception as e:  # a torn sections.json read
            errors.append(repr(e))
    t.join()
    return at_rest, sizes, errors


def test_fixture_selects_every_section(ctx):
    _tagger(HERE / 'tag_sections.py', ctx, 0).run()
    out = _selector(ctx).select(QUERY)
    assert 'mode=bm25, core=0, selected=8' in out
    for heading in ('## PERMANENT DECISIONS', '## STANDING RULES', '## FLEET FACTS',
                    '### architecture:tagger', '## RECENT EPISODES'):
        assert heading in out


def test_orig_tagger_truncates_selector_output(ctx):
    at_rest, sizes, errors = _sizes_during_retag(FIX / 'tag_sections.orig.py', ctx)
    assert sizes, 'selector never ran during the re-tag window'
    assert errors or min(sizes) < at_rest


def test_fixed_tagger_keeps_selector_output_complete(ctx):
    at_rest, sizes, errors = _sizes_during_retag(HERE / 'tag_sections.py', ctx)
    assert sizes, 'selector never ran during the re-tag window'
    assert errors == []
    assert set(sizes) == {at_rest}


def test_fixed_tagger_prunes_only_dropped_ids(ctx):
    ts = _tagger(HERE / 'tag_sections.py', ctx, 0)
    ts.run()
    keep = ctx / 'sections' / '021_0-quick-reference-card.md.bak.20260701_213229'
    keep.write_text('backup, not an index id')
    mem = ctx / 'claude_memory.md'
    mem.write_text(mem.read_text().split('## HAND PROSE')[0])
    ts.run()
    names = sorted(os.listdir(ctx / 'sections'))
    assert len([n for n in names if n.endswith('.md')]) == 7
    assert not any(n.startswith('007_') for n in names)
    assert keep.name in names
    assert not any('.tmp.' in n for n in os.listdir(ctx))
    assert not any('.tmp.' in n for n in names)
