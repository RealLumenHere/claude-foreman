#!/usr/bin/env python3
"""wq#4402 probe (wq#2630: must be able to fail). Asserts undeclared_sibling_refs()
fires on a reference to an OPEN sibling and stays silent on a reference to a DONE
sibling -- both outcomes are exercised, so the probe can fail either direction."""
import argparse
import re
import sqlite3


def load_detector(wq_path):
    src = open(wq_path).read()
    start = src.index('_WQ_REF_RE = re.compile')
    end = src.index('# --- end wq#4402 helpers ---')
    ns = {'re': re}
    exec(src[start:end], ns)
    return ns['undeclared_sibling_refs']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--wq', default='/home/lumen/bin/wq')
    a = ap.parse_args()
    undeclared_sibling_refs = load_detector(a.wq)

    c = sqlite3.connect(':memory:')
    c.execute('CREATE TABLE tasks(id INTEGER PRIMARY KEY, status TEXT)')
    c.execute("INSERT INTO tasks VALUES (9001,'open')")
    c.execute("INSERT INTO tasks VALUES (9002,'done')")

    r1 = undeclared_sibling_refs(c, 'INTEGRATE: wrap wq#9001 deliverable',
                                  'This wraps wq#9001 output.')
    assert r1 == [('9001', 'open')], f"expected open-sibling fire, got {r1}"

    r2 = undeclared_sibling_refs(c, 'Some ticket',
                                  'Per wq#9002 this was already handled.')
    assert r2 == [], f"expected no fire on done sibling, got {r2}"

    print('PROBE_PASS: fires on open sibling, silent on done sibling')


if __name__ == '__main__':
    main()
