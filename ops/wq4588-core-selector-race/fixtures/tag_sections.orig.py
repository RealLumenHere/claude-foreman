#!/usr/bin/env python3
"""
tag_sections.py - Split claude_memory.md into sections, embed each, and build an index.

For each H2 (##) section, compute:
  - section_id (slug)
  - title
  - content (full section text)
  - token_estimate (len/4 approximation)
  - embedding (via PBox Ollama nomic-embed-text)
  - core_tag (True if has #CORE marker or matches hardcoded always-include set)

Writes:
  - /home/lumen/minerva/context/sections.json (index)
  - /home/lumen/minerva/context/claude_memory_index.md (human-readable index)

Runs on memory file change (called by a systemd path unit or cron).
"""
import json, re, hashlib, os, sys, requests
from pathlib import Path
from datetime import datetime, timezone
import numpy as np

MEM = Path('/home/lumen/claude_memory.md')
OUT_JSON = Path('/home/lumen/minerva/context/sections.json')
SECTIONS_DIR = Path("/home/lumen/minerva/context/sections")
OUT_INDEX = Path('/home/lumen/minerva/context/claude_memory_index.md')
LOG = Path('/home/lumen/minerva/logs/context.log')
# 2026-06-07: PBox retired — embeddings now via local Ollama on Lumen (var name kept for diff-minimalism).
PBOX_OLLAMA = 'http://localhost:11434'
EMBED_MODEL = 'bge-m3'   # set by swap_embedder.sh 20260625_043432

# Sections that are ALWAYS included regardless of query relevance
CORE_SECTION_TITLES = {
    'MINERVA_SYSTEM',
    'MINERVA AUTO-BRIEF (most recent daily summary)',
    '🧭 MCP Toolkit Index — READ THIS FIRST',
    '1. SESSION START CHECKLIST',
    '0. QUICK REFERENCE CARD',
    'CLASSIFIER-SHAPE ROUTING RULE',
}

def log(m):
    ts = datetime.now(timezone.utc).strftime('%H:%M:%S UTC')
    line = f'[{ts}] {m}'
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')

def embed(text):
    try:
        r = requests.post(f'{PBOX_OLLAMA}/api/embed',
            json={'model': EMBED_MODEL, 'input': text[:4000]}, timeout=20)
        if r.status_code != 200:
            return None
        vec = r.json().get('embeddings', [[]])[0]
        return np.array(vec, dtype=np.float32) if vec else None
    except Exception as e:
        log(f'embed err: {e}')
        return None

def slugify(s):
    s = re.sub(r'[^\w\s-]', '', s).strip().lower()
    return re.sub(r'[-\s]+', '-', s)[:80]

def is_core(title):
    for core in CORE_SECTION_TITLES:
        if core.lower() in title.lower():
            return True
    return '#CORE' in title or '#core' in title

# Memory v2 Phase 1 (2026-06-25): durable #CORE invariants-floor audit.
# Among dated snapshot blocks (CURRENT STATE OVERRIDES / STATE MARKERS) only the
# NEWEST-dated block stays core; older ones auto-demote to the retrieved tier.
# This applies the invalidation philosophy to core membership itself: every new
# override block silently demotes its predecessors. Demoted blocks remain in the
# corpus and are still retrievable on demand -- they just stop force-loading.
DATED_CORE_GROUPS = {
    'overrides': 'current state overrides',
    'markers':   'state markers',
}
def superseded_core_indices(sections):
    import re as _re
    groups = {}  # group -> list of (date_str, idx)
    for i, s in enumerate(sections):
        t = s['title'].lower()
        for g, needle in DATED_CORE_GROUPS.items():
            if needle in t:
                m = _re.search(r'(\d{4}-\d{2}-\d{2})', s['title'])
                if m:
                    groups.setdefault(g, []).append((m.group(1), i))
    demote = set()
    for g, items in groups.items():
        if len(items) <= 1:
            continue
        newest = max(items, key=lambda x: x[0])[1]
        for _d, idx in items:
            if idx != newest:
                demote.add(idx)
    return demote

# wq#1467: sections/*.md and sections.json are readable by any worker holding dir(/home/lumen/minerva) and are
# injected into contexts, so they must never carry live secrets. Two rules, applied inside split_sections() so
# BOTH writers (tag_sections.run and reindex_surgical.py) inherit them:
#   1. a section whose HEADING says credentials/secrets AND whose body holds a live secret-shaped value is dropped
#      entirely (the value-scan keeps procedural notes such as "GITEA CREDENTIAL - one file, one rotation point");
#   2. any secret-shaped value left in any other section is masked.
SECRET_HEADING_RE = re.compile(r'\b(credentials?|secrets?)\b', re.I)
SECRET_VALUE_RE = re.compile(
    r'cfut_[A-Za-z0-9]{20,}'                 # Cloudflare API token
    r'|sk-or-[A-Za-z0-9_-]{20,}'             # OpenRouter key
    r'|ntn_[A-Za-z0-9]{20,}'                 # Notion token
    r'|gh[pousr]_[A-Za-z0-9]{30,}'           # GitHub tokens
    r'|glpat-[A-Za-z0-9_-]{20,}'             # GitLab PAT
    r'|sk-ant-[A-Za-z0-9_-]{20,}'            # Anthropic key
    r'|xox[baprs]-[A-Za-z0-9-]{20,}'         # Slack token
    r'|\b[0-9a-f]{24}:[0-9a-f]{64}\b'        # Ghost admin key id:secret
)

def _drop_and_mask_secrets(sections):
    kept = []
    for s in sections:
        body = '\n'.join(s['lines'])
        if SECRET_HEADING_RE.search(s['title']) and SECRET_VALUE_RE.search(body):
            try:
                log(f"secrets guard: dropped section {s['title']!r} (heading + live secret value)")
            except Exception:
                pass
            continue
        if SECRET_VALUE_RE.search(body):
            s['lines'] = [SECRET_VALUE_RE.sub('<REDACTED>', ln) for ln in s['lines']]
        kept.append(s)
    return kept

def split_sections(text):
    """Split on ## (H2 only) headers. Keep H2+H3+... within a section."""
    return _drop_and_mask_secrets(_split_sections_raw(text))

def _split_sections_raw(text):
    sections = []
    current = None
    for line in text.split('\n'):
        # Only split on H2 (not H3/H4)
        if re.match(r'^##\s+(?!#)', line):
            if current:
                sections.append(current)
            title = re.sub(r'^##\s+', '', line).strip()
            current = {'title': title, 'lines': [line]}
        elif re.match(r'^#\s+(?!#)', line) and not current:
            # H1 as first section
            title = re.sub(r'^#\s+', '', line).strip()
            current = {'title': title, 'lines': [line]}
        else:
            if current is None:
                current = {'title': '[preamble]', 'lines': []}
            current['lines'].append(line)
    if current:
        sections.append(current)
    return sections

def run():
    # Clean old sections
    if SECTIONS_DIR.exists():
        for p in SECTIONS_DIR.glob("*.md"):
            p.unlink()
    log('=== tag_sections ===')
    text = MEM.read_text()
    sections = split_sections(text)
    log(f'Parsed {len(sections)} sections')
    demote_idx = superseded_core_indices(sections)
    if demote_idx:
        log(f'#CORE audit: demoting {len(demote_idx)} superseded dated block(s): {sorted(demote_idx)}')

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    index = []
    index_md_lines = [
        '# Claude Memory — Section Index',
        f'_Generated {datetime.now(timezone.utc).isoformat()}_  ',
        f'Total sections: {len(sections)}  ',
        f'Full file: /home/lumen/claude_memory.md',
        '',
        '## Core sections (always injected)',
    ]
    index_md_core = []
    index_md_other = []

    for i, s in enumerate(sections):
        content = '\n'.join(s['lines'])
        token_est = len(content) // 4
        sid = f'{i:03d}_{slugify(s["title"])}'
        core = is_core(s['title']) and i not in demote_idx

        emb = embed(f"{s['title']}. {content[:2000]}")
        emb_b64 = None
        if emb is not None:
            import base64
            emb_b64 = base64.b64encode(emb.tobytes()).decode()

        entry = {
            'id': sid,
            'title': s['title'],
            'token_estimate': token_est,
            'char_len': len(content),
            'core': core,
            'embedding_b64': emb_b64,
            'content_lc': content.lower(),  # 2026-06-25 Memory v2 P0: inline lowercased content for BM25 hybrid retrieval (corpus tiny; avoids hot-path file reads)
        }
        index.append(entry)

        # Write each section to its own file for fast retrieval
        sec_path = Path(f'/home/lumen/minerva/context/sections/{sid}.md')
        sec_path.parent.mkdir(parents=True, exist_ok=True)
        sec_path.write_text(content)

        line = f'- **[{sid}]**  — {s["title"]}{" [CORE]" if core else ""}'
        if core:
            index_md_core.append(line)
        else:
            index_md_other.append(line)

    total_tokens = sum(e['token_estimate'] for e in index)
    core_tokens = sum(e['token_estimate'] for e in index if e['core'])

    index_md_lines.extend(index_md_core)
    index_md_lines.extend(['', '## All other sections (retrieved on demand)', ''])
    index_md_lines.extend(index_md_other)
    index_md_lines.extend([
        '',
        '---',
        f'**Total estimated tokens:** {total_tokens}',
        f'**Core-only tokens (always-injected floor):** {core_tokens}',
        f'**Reduction if only core is loaded:** {100 * (1 - core_tokens/max(total_tokens,1)):.1f}%',
    ])

    OUT_JSON.write_text(json.dumps(index, indent=2))
    OUT_INDEX.write_text('\n'.join(index_md_lines))
    log(f'Wrote index: {OUT_JSON} ({len(index)} sections, {total_tokens} tokens, {core_tokens} core)')
    log(f'Wrote human index: {OUT_INDEX}')

if __name__ == '__main__':
    run()
