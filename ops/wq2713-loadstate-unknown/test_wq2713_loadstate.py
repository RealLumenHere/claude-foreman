"""wq#2713: LoadState lookup failure must read 'unknown', never 'not-found';
healer files the delete-row ticket only after STALE_CONFIRM consecutive confirmed reads."""
import ast, os, sys, types, subprocess as real_sp
# wq#3222: this dir, not the primary tree's copy, and nothing at import — the old
# module-level run parsed pytest's own argv as paths and sys.exit'd during collection.
D = os.path.dirname(os.path.abspath(__file__)) + '/'

def load(path, names, extra):
    tree = ast.parse(open(path).read())
    keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in names)
            or (isinstance(n, ast.Assign) and any(getattr(t, 'id', '') in names for t in n.targets))]
    ns = dict(extra); exec(compile(ast.Module(keep, []), path, 'exec'), ns); return ns

class R:
    def __init__(s, rc, out): s.returncode, s.stdout = rc, out
def fake(mode):
    def run(cmd, **kw):
        if cmd[0] != 'systemctl': CALLS.append(cmd); return R(0, '')
        if mode == 'timeout': raise real_sp.TimeoutExpired(cmd, 10)
        if mode == 'rc1': return R(1, '')
        if mode == 'empty': return R(0, '')
        return R(0, f'LoadState={mode}\n')
    return types.SimpleNamespace(run=run, TimeoutExpired=real_sp.TimeoutExpired)
class L:
    def warning(s, m): pass
    info = warning
FAIL = 0
CALLS = []
def chk(name, got, want):
    global FAIL; ok = got == want; FAIL |= not ok
    print(('PASS' if ok else 'FAIL'), name, repr(got), '' if ok else f'want {want!r}')

def main(target=D + 'lumen_self_healer.py', split=D + 'split_watch.py'):
    global FAIL
    FAIL = 0
    CALLS.clear()
    for path, fn in [(target, '_unit_load_state'), (split, '_load_state')]:
        tag = path.split('/')[-1]
        for mode, want in [('timeout','unknown'),('rc1','unknown'),('empty','unknown'),
                           ('loaded','loaded'),('not-found','not-found'),('masked','masked')]:
            ns = load(path, {fn}, {'subprocess': fake(mode)})
            chk(f'{tag}:{mode}', ns[fn]('x.service'), want)

    CALLS.clear()
    ns = load(target, {'_file_stale_unit_ticket','_stale_alerted','_stale_seen','STALE_CONFIRM'},
              {'subprocess': fake('loaded'), 'log': L(), 'WQ_BIN': 'wq', 'WATCH_TABLE': 't.yaml'})
    if 'STALE_CONFIRM' not in ns:
        chk('confirm-gate present', False, True)
    else:
        for i in range(ns['STALE_CONFIRM'] - 1):
            ns['_file_stale_unit_ticket']('u', 'not-found')
        chk('no ticket before confirm', len(CALLS), 0)
        ns['_file_stale_unit_ticket']('u', 'not-found'); chk('ticket at confirm', len(CALLS), 1)
        ns['_file_stale_unit_ticket']('u', 'not-found'); chk('dedup after file', len(CALLS), 1)
    print('RESULT', 'ALL_PASS' if not FAIL else 'FAILED')
    return FAIL

def test_wq2713_loadstate():
    assert main() == 0

if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:3]))
