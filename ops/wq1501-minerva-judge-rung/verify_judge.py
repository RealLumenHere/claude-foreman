#!/usr/bin/env python3
"""wq#1501 — replayable checks for the Minerva verify judge.

Three checks, each printed as one line, exit 0 only when every requested check
passes. Runs as lumen (journalctl + systemctl show are read-only).

  env      effective service env: primary judge set, fallback rung empty or a
           real LiteLLM model (never a phantom such as mistral-small).
  journal  verify lines since --since: ReadTimeout share and phantom-fallback
           4xx count. Fails on any fallback 4xx (a rung that cannot answer) or
           when ReadTimeout share exceeds --max-timeout-share.
  window   wq#1528: the three sizes must stay ordered, judged against what the
           judge actually sees (minerva_factpack.judge_digest compaction):
             compact digest + CLOSED_WORLD + facts reserve <= FACTPACK_CAP
             <= VERIFY_JUDGE_CTX_CHARS <= rung window (--rung-max chars,
             qwen36-local n_ctx 8192 tokens).
           Otherwise build_context drops the digest tail and every retrieved
           fact (wq#1383 window class, 3rd occurrence). WARN unless --strict.

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


FACTPACK_PATHS = ('/home/lumen/minerva/minerva_factpack.py',            # live
                  '/home/lumen/research/minerva-ref/minerva_factpack.py')  # worker mirror


def _load_factpack():
    import importlib.util
    for p in FACTPACK_PATHS:
        if not os.path.exists(p):
            continue
        try:
            spec = importlib.util.spec_from_file_location('_mfp_check', p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)      # constants only; probes are lazy
            return mod
        except Exception:
            continue
    return None


def check_window(digest_path, reserve, rung_max, strict):
    env = service_env()
    # The code reads VERIFY_JUDGE_CTX_CHARS; JUDGE_CTX_CHARS is the legacy env
    # name (factpack.conf 2026-07-27) kept as fallback for pre-wq#1528 hosts.
    window = int(env.get('VERIFY_JUDGE_CTX_CHARS') or env.get('JUDGE_CTX_CHARS') or 0)
    if not os.path.exists(digest_path):
        print(f'window SKIP digest {digest_path} not readable here')
        return True
    fp = _load_factpack()
    if fp is not None:
        cap = int(env.get('FACTPACK_CAP') or fp.CAP)
        reserve = int(env.get('FACTPACK_FACTS_RESERVE')
                      or getattr(fp, 'FACTS_RESERVE', reserve))
        with open(digest_path) as fh:
            digest = len(fp.judge_digest(fh.read()))     # what the judge sees
        need = digest + len(getattr(fp, 'CLOSED_WORLD', '')) + reserve
        kind = 'compact'
    else:                                # factpack unreadable: raw-size fallback
        cap = int(env.get('FACTPACK_CAP') or window - reserve)
        digest = os.path.getsize(digest_path)
        need = digest + reserve
        kind = 'raw'
    problems = []
    if cap < need:
        problems.append(f'FACTPACK_CAP {cap} < {kind} digest + closed-world + '
                        f'facts reserve {need} ({need - cap} over): digest tail '
                        f'dropped and retrieved facts starved')
    if cap > window:
        problems.append(f'FACTPACK_CAP {cap} > judge window {window}: factpack '
                        f'context truncated at the window')
    if window > rung_max:
        problems.append(f'window {window} > rung max {rung_max} chars '
                        f'(qwen36-local n_ctx 8192 tokens): prompt overruns n_ctx')
    ok = not problems
    tag = 'OK' if ok else ('FAIL' if strict else 'WARN')
    print(f'window {tag} window={window} cap={cap} reserve={reserve} '
          f'digest({kind})={digest} rung_max={rung_max}'
          + ('' if ok else ' :: ' + '; '.join(problems)))
    return ok or not strict


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('checks', nargs='*', choices=['env', 'journal', 'window'])
    ap.add_argument('--since', default='2026-09-08 10:45')
    ap.add_argument('--max-timeout-share', type=float, default=0.10)
    ap.add_argument('--models-file', default=None,
                    help='one LiteLLM model name per line; fallback must be listed')
    ap.add_argument('--digest', default=DIGEST_DEFAULT)
    ap.add_argument('--reserve', type=int, default=4000,
                    help='facts budget build_context reserves before the digest '
                         '(fallback when FACTPACK_FACTS_RESERVE/minerva_factpack unavailable)')
    ap.add_argument('--rung-max', type=int, default=23500,
                    help='judge rung window in chars (qwen36-local n_ctx 8192 tokens '
                         'minus completion + safety at ~3.0 chars/token)')
    ap.add_argument('--strict', action='store_true')
    a = ap.parse_args()
    checks = a.checks or ['env', 'journal', 'window']
    results = []
    if 'env' in checks:
        results.append(check_env(a.models_file))
    if 'journal' in checks:
        results.append(check_journal(a.since, a.max_timeout_share))
    if 'window' in checks:
        results.append(check_window(a.digest, a.reserve, a.rung_max, a.strict))
    sys.exit(0 if all(results) else 1)


if __name__ == '__main__':
    main()
