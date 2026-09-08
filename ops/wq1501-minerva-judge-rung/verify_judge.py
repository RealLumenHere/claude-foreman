#!/usr/bin/env python3
"""wq#1501 — replayable checks for the Minerva verify judge.

Three checks, each printed as one line, exit 0 only when every requested check
passes. Runs as lumen (journalctl + systemctl show are read-only).

  env      effective service env: primary judge set, fallback rung empty or a
           real LiteLLM model (never a phantom such as mistral-small).
  journal  verify lines since --since: ReadTimeout share and phantom-fallback
           4xx count. Fails on any fallback 4xx (a rung that cannot answer) or
           when ReadTimeout share exceeds --max-timeout-share.
  window   JUDGE_CTX_CHARS minus --reserve must cover the proposal digest, else
           build_context drops the digest tail and every retrieved fact
           (wq#1383 window class, 3rd occurrence). WARN unless --strict.

Usage:
  verify_judge.py [env] [journal] [window] [--since TS] [--strict]
  (no check names = all three)
"""
import argparse
import os
import re
import subprocess
import sys

UNIT = 'minerva-proposal.service'
PHANTOM_RUNGS = {'mistral-small'}          # names that were never a LiteLLM rung
DIGEST_DEFAULT = '/home/lumen/minerva/lumen_state.txt'
VERIFY_RE = re.compile(r'verify \[')
TIMEOUT_RE = re.compile(r'judge_err:ReadTimeout')
FALLBACK_4XX_RE = re.compile(r'fallback:judge_http_4\d\d')
JUDGE_MS_RE = re.compile(r'judge_ms=\[([0-9, ]+)\]')


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout


def service_env():
    out = _run(['systemctl', 'show', '-p', 'Environment', UNIT])
    env = {}
    for tok in out.replace('Environment=', '', 1).split():
        if '=' in tok:
            k, v = tok.split('=', 1)
            env[k] = v                      # later drop-ins win, like systemd
    return env


def check_env(models_file=None):
    env = service_env()
    primary = env.get('VERIFY_JUDGE_MODEL', '')
    fallback = env.get('VERIFY_JUDGE_MODEL_2')
    problems = []
    if not primary:
        problems.append('VERIFY_JUDGE_MODEL unset')
    if fallback in PHANTOM_RUNGS:
        problems.append(f'VERIFY_JUDGE_MODEL_2={fallback} is a phantom rung')
    if fallback and models_file and os.path.exists(models_file):
        known = {l.strip() for l in open(models_file) if l.strip()}
        if fallback not in known:
            problems.append(f'VERIFY_JUDGE_MODEL_2={fallback} not in {models_file}')
    timeout = int(env.get('VERIFY_JUDGE_TIMEOUT', '40') or 40)
    if timeout < 120:
        problems.append(f'VERIFY_JUDGE_TIMEOUT={timeout} < 120s (local rung cold prefill)')
    status = 'FAIL' if problems else 'OK'
    print(f'env {status} primary={primary or "-"} fallback={fallback if fallback else "(none)"} '
          f'timeout={timeout} samples={env.get("VERIFY_JUDGE_SAMPLES", "?")}'
          + (' :: ' + '; '.join(problems) if problems else ''))
    return not problems


def check_journal(since, max_timeout_share):
    out = _run(['journalctl', '-u', UNIT, '--since', since, '--no-pager'])
    lines = [l for l in out.splitlines() if VERIFY_RE.search(l)]
    n = len(lines)
    timeouts = sum(1 for l in lines if TIMEOUT_RE.search(l))
    phantom = sum(1 for l in lines if FALLBACK_4XX_RE.search(l))
    ms = []
    for l in lines:
        m = JUDGE_MS_RE.search(l)
        if m:
            ms.append([int(x) for x in m.group(1).replace(' ', '').split(',') if x])
    share = (timeouts / n) if n else 0.0
    problems = []
    if phantom:
        problems.append(f'{phantom} phantom-fallback 4xx')
    if n and share > max_timeout_share:
        problems.append(f'ReadTimeout share {share:.0%} > {max_timeout_share:.0%}')
    first = [v[0] for v in ms if v]
    p_first = f' judge_ms[0] max={max(first)}' if first else ''
    status = 'FAIL' if problems else ('OK' if n else 'EMPTY')
    print(f'journal {status} since={since} verify={n} readtimeout={timeouts} '
          f'phantom4xx={phantom}{p_first}'
          + (' :: ' + '; '.join(problems) if problems else ''))
    return not problems


def check_window(digest_path, reserve, strict):
    env = service_env()
    window = int(env.get('JUDGE_CTX_CHARS', '0') or 0)
    cap = window - reserve
    if not os.path.exists(digest_path):
        print(f'window SKIP digest {digest_path} not readable here')
        return True
    digest = os.path.getsize(digest_path)
    ok = cap >= digest
    tag = 'OK' if ok else ('FAIL' if strict else 'WARN')
    print(f'window {tag} JUDGE_CTX_CHARS={window} cap={cap} digest={digest}'
          + ('' if ok else f' :: digest tail dropped and retrieved facts starved ({digest - cap} over)'))
    return ok or not strict


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('checks', nargs='*', choices=['env', 'journal', 'window'])
    ap.add_argument('--since', default='2026-09-08 10:45')
    ap.add_argument('--max-timeout-share', type=float, default=0.10)
    ap.add_argument('--models-file', default=None,
                    help='one LiteLLM model name per line; fallback must be listed')
    ap.add_argument('--digest', default=DIGEST_DEFAULT)
    ap.add_argument('--reserve', type=int, default=500,
                    help='chars build_context keeps back from JUDGE_CTX_CHARS (FACTPACK_CAP = window - reserve)')
    ap.add_argument('--strict', action='store_true')
    a = ap.parse_args()
    checks = a.checks or ['env', 'journal', 'window']
    results = []
    if 'env' in checks:
        results.append(check_env(a.models_file))
    if 'journal' in checks:
        results.append(check_journal(a.since, a.max_timeout_share))
    if 'window' in checks:
        results.append(check_window(a.digest, a.reserve, a.strict))
    sys.exit(0 if all(results) else 1)


if __name__ == '__main__':
    main()
