#!/usr/bin/env python3
"""End-to-end check that a dsh agent loop drives tools through LiteLLM (wq#4824).

Boots `dsh --profile headless` with litellm.patch.yml in a fresh scratch directory
that holds one file with a random nonce, and asks for nonce * 3 using the tools. The
run passes only if the event stream shows at least one tool call AND the final answer
holds the right product, which the model cannot know without reading the file.

The key is copied from the 0600 env file into the child's environment; it is never
printed or put on a command line.

    dsh_e2e.py [--model deepseek-v4-flash] [--out e2e_result.json]
"""
import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from toolcall_probe import read_key  # noqa: E402

PATCH = os.path.join(HERE, 'litellm.patch.yml')
SCRATCH = os.getenv('DSH_E2E_SCRATCH', '/tmp/fworker-wq4824')


def model_patch(model, workdir):
    """Overlay that swaps the default model, written next to the run, not into the repo."""
    path = os.path.join(workdir, '..', os.path.basename(workdir) + '.model.yml')
    with open(path, 'w') as fh:
        fh.write(f'- id: agent-default-model\n  config:\n    provider: litellm\n    model: {model}\n')
    return os.path.abspath(path)


def tool_calls(events):
    """Names of the tools the agent called, from dsh 0.2.1's `tool_call` run events."""
    return [ev.get('name') or ev.get('tool') or '?' for ev in events
            if ev.get('type') == 'tool_call']


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--model', default='deepseek-v4-flash')
    ap.add_argument('--out')
    ap.add_argument('--timeout', type=int, default=300)
    a = ap.parse_args()

    os.makedirs(SCRATCH, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix='dsh-e2e-', dir=SCRATCH)
    nonce = random.randint(100000, 999999)
    with open(os.path.join(workdir, 'nonce.txt'), 'w') as fh:
        fh.write(f'{nonce}\n')
    task = ('Use your file or shell tools to read nonce.txt in the current directory. '
            'Reply with only the integer it contains multiplied by 3.')

    env = dict(os.environ, LITELLM_PILOT_KEY=read_key())
    cmd = ['dsh', '--profile', 'headless', '--patch', PATCH,
           '--patch', model_patch(a.model, workdir), '--json', task]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True,
                           timeout=a.timeout)
        rc, stdout, stderr = r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired as e:
        rc, stdout, stderr = 'timeout', e.stdout or '', e.stderr or ''
        stdout = stdout.decode() if isinstance(stdout, bytes) else stdout
        stderr = stderr.decode() if isinstance(stderr, bytes) else stderr

    events = []
    for line in stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    want = str(nonce * 3)
    tail = json.dumps(events[-3:]) if events else stdout[-600:]
    tools = tool_calls(events)
    row = {
        'model': a.model, 'rc': rc, 'seconds': round(time.time() - t0, 1),
        'n_events': len(events), 'event_types': sorted({e.get('type', '?') for e in events}),
        'tool_calls': tools, 'answer_ok': want in tail,
        'stderr_tail': stderr[-400:],
    }
    row['pass'] = rc == 0 and row['answer_ok'] and bool(tools)
    print(json.dumps(row, indent=1))
    if a.out:
        with open(a.out, 'w') as fh:
            json.dump(dict(row, probed_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())),
                      fh, indent=1)
    return 0 if row['pass'] else 1


if __name__ == '__main__':
    sys.exit(main())
