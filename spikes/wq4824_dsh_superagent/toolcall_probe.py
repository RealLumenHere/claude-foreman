#!/usr/bin/env python3
"""Tool-calling fidelity probe for LiteLLM, run before trusting it under dsh-superagent (wq#4824).

dsh's pi-ai adapter speaks `openai-completions` and streams, so each model is probed
three ways against the same two-tool schema:

  call    non-streaming: the model must pick `add_numbers` with integer args 17 and 25
  stream  streaming: the tool-call deltas must reassemble into the same call
  round   second turn: given the tool result 42, the reply must say 42 and stop

A model passes only if all three hold. The key is read from a 0600 env file and only
ever placed in a header; it is never printed.

    toolcall_probe.py models
    toolcall_probe.py probe MODEL [MODEL ...] [--out results.json]
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.getenv('LITELLM_BASE', 'http://localhost:4000/v1')
KEY_FILE = os.getenv('LITELLM_KEY_FILE', '/home/lumen/agent/.env')
KEY_NAME = os.getenv('LITELLM_KEY_NAME', 'LITELLM_MASTER_KEY')

TOOLS = [
    {'type': 'function', 'function': {
        'name': 'add_numbers',
        'description': 'Add two integers and return the sum.',
        'parameters': {'type': 'object', 'properties': {
            'a': {'type': 'integer'}, 'b': {'type': 'integer'}},
            'required': ['a', 'b']}}},
    {'type': 'function', 'function': {
        'name': 'get_weather',
        'description': 'Current weather for a city.',
        'parameters': {'type': 'object', 'properties': {
            'city': {'type': 'string'}}, 'required': ['city']}}},
]
PROMPT = 'Use the add_numbers tool to compute 17 + 25. Do not compute it yourself.'


def read_key():
    try:
        with open(KEY_FILE) as fh:
            for line in fh:
                if line.startswith(KEY_NAME + '='):
                    return line.split('=', 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return ''


def request(path, body=None, stream=False, timeout=120):
    hdr = {'Content-Type': 'application/json'}
    key = read_key()
    if key:
        hdr['Authorization'] = 'Bearer ' + key
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=hdr)
    resp = urllib.request.urlopen(req, timeout=timeout)
    if not stream:
        with resp:
            return json.load(resp)
    return resp


def check_call(call):
    """Return an error string, or '' when the call is add_numbers(17, 25)."""
    name = call.get('function', {}).get('name')
    if name != 'add_numbers':
        return f'wrong tool {name!r}'
    try:
        args = json.loads(call['function'].get('arguments') or '{}')
    except ValueError:
        return 'arguments not JSON'
    if args.get('a') != 17 or args.get('b') != 25:
        return f'wrong args {args}'
    if not call.get('id'):
        return 'missing tool_call id'
    return ''


def probe_call(model):
    out = request('/chat/completions', {
        'model': model, 'messages': [{'role': 'user', 'content': PROMPT}],
        'tools': TOOLS, 'tool_choice': 'auto', 'max_tokens': 400})
    choice = out['choices'][0]
    calls = choice['message'].get('tool_calls') or []
    if not calls:
        return {'ok': False, 'error': 'no tool_calls', 'finish': choice.get('finish_reason')}, None
    err = check_call(calls[0])
    return {'ok': not err, 'error': err, 'finish': choice.get('finish_reason'),
            'n_calls': len(calls)}, calls[0]


def probe_stream(model):
    resp = request('/chat/completions', {
        'model': model, 'messages': [{'role': 'user', 'content': PROMPT}],
        'tools': TOOLS, 'tool_choice': 'auto', 'max_tokens': 400, 'stream': True},
        stream=True)
    calls, finish, chunks = {}, None, 0
    with resp:
        for raw in resp:
            line = raw.decode().strip()
            if not line.startswith('data:'):
                continue
            payload = line[5:].strip()
            if payload == '[DONE]':
                break
            chunks += 1
            choice = (json.loads(payload).get('choices') or [{}])[0]
            finish = choice.get('finish_reason') or finish
            for d in (choice.get('delta') or {}).get('tool_calls') or []:
                slot = calls.setdefault(d.get('index', 0),
                                        {'id': '', 'function': {'name': '', 'arguments': ''}})
                slot['id'] = slot['id'] or d.get('id') or ''
                fn = d.get('function') or {}
                slot['function']['name'] += fn.get('name') or ''
                slot['function']['arguments'] += fn.get('arguments') or ''
    if not calls:
        return {'ok': False, 'error': 'no streamed tool_calls', 'finish': finish, 'chunks': chunks}
    err = check_call(calls[min(calls)])
    return {'ok': not err, 'error': err, 'finish': finish, 'chunks': chunks}


def probe_round(model, call):
    out = request('/chat/completions', {
        'model': model, 'tools': TOOLS, 'max_tokens': 400, 'messages': [
            {'role': 'user', 'content': PROMPT},
            {'role': 'assistant', 'content': None, 'tool_calls': [call]},
            {'role': 'tool', 'tool_call_id': call['id'], 'content': '42'}]})
    choice = out['choices'][0]
    text = choice['message'].get('content') or ''
    again = choice['message'].get('tool_calls')
    ok = '42' in text and not again
    return {'ok': ok, 'error': '' if ok else ('re-called tool' if again else 'answer lacks 42'),
            'finish': choice.get('finish_reason')}


def probe(model):
    row = {'model': model}
    t0 = time.time()
    for name, fn in (('call', probe_call), ('stream', probe_stream)):
        try:
            res = fn(model)
            if name == 'call':
                res, call = res
                row['_call'] = call
            row[name] = res
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            row[name] = {'ok': False, 'error': f'{type(e).__name__}: {str(e)[:160]}'}
    call = row.pop('_call', None)
    if row['call']['ok'] and call:
        try:
            row['round'] = probe_round(model, call)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            row['round'] = {'ok': False, 'error': f'{type(e).__name__}: {str(e)[:160]}'}
    else:
        row['round'] = {'ok': False, 'error': 'skipped: call failed'}
    row['pass'] = all(row[k]['ok'] for k in ('call', 'stream', 'round'))
    row['seconds'] = round(time.time() - t0, 1)
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('models')
    p = sub.add_parser('probe')
    p.add_argument('models', nargs='+')
    p.add_argument('--out')
    a = ap.parse_args()
    if a.cmd == 'models':
        print('\n'.join(sorted(m['id'] for m in request('/models')['data'])))
        return 0
    rows = [probe(m) for m in a.models]
    for r in rows:
        print(f"{'PASS' if r['pass'] else 'FAIL'} {r['model']:<32} "
              + ' '.join(f"{k}={'ok' if r[k]['ok'] else r[k]['error']}"
                         for k in ('call', 'stream', 'round')))
    if a.out:
        with open(a.out, 'w') as fh:
            json.dump({'base': BASE, 'probed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                       'results': rows}, fh, indent=1)
    return 0 if all(r['pass'] for r in rows) else 1


if __name__ == '__main__':
    sys.exit(main())
