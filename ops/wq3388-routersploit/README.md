# wq#3388 — install RouterSploit (embedded exploit framework)

For recovered/EOL router work: Broadcom RT-AX86U, Cisco EOL gear, anything else
that shows up dead or half-bricked. `git clone` + a dedicated venv (not
user/system site-packages), per the `ops/wqNNNN-<slug>/install.sh` deploy
contract (`../VERIFY_CONTRACT.md`, wq#1696).

**Live on Lumen as of this ticket**: `/home/lumen/bin/routersploit` (clone +
`.venv`) and the `/home/lumen/bin/rsf` wrapper. `install.sh` ran for real
during this build (not just a scratch test) — `/home/lumen/bin/rsf -h` prints
usage and exits 0 on the box right now.

## What's here

- `install.sh` — clones `threat9/routersploit` to `/home/lumen/bin/routersploit`
  (not `/home/lumen/build`: this BUILD-lane worktree has no write access
  outside its own tree plus a short `--add-dir` list, and `/home/lumen/bin` is
  on that list — also the existing convention for heavier vendored RE tools,
  see `retdec-5.0/`, `renode-dist/` there), builds `.venv` there, installs
  `requirements.txt` **plus `telnetlib3` and `"setuptools<81"`** (see below),
  drops a thin wrapper at `/home/lumen/bin/rsf`, then runs `rsf.py -h` as a
  live startup check before it reports done.
- `rsf-wrapper` — the file copied to `/home/lumen/bin/rsf`; `cd`s into the
  install dir first since `rsf.py` opens `routersploit.log` relative to cwd.
- `install.sh --verify` — read-only: checks the venv interpreter and `rsf.py`
  exist, the wrapper is byte-identical to the shipped copy, and
  `.venv/bin/python3 -c "import routersploit.interpreter"` succeeds. That
  import — not bare `import routersploit`, and not run against the system
  python3 — is the actual regression surface; see next section for why both
  qualifiers matter.

## The compatibility issues (found during this build, not in upstream's tracker)

Upstream `requirements.txt` (`requests`, `paramiko`, `pysnmp`, `pycryptodome`)
installs clean on Python 3.14.4 — pycryptodome ships 3.14 wheels, no build-from-
source needed. But starting RouterSploit for real in a *clean* venv hits two
stacked failures, neither of which a bare `import routersploit` catches:
`routersploit/__init__.py` is empty, so only `import routersploit.interpreter`
(what `rsf.py` itself does) walks the chain that breaks.

**1. `telnetlib3` missing:**

```
routersploit/core/exploit/shell.py:2: import telnetlib
ModuleNotFoundError: No module named 'telnetlib'
  (falls back to) import telnetlib3 as telnetlib
ModuleNotFoundError: No module named 'telnetlib3'
```

`telnetlib` was removed from the stdlib in Python 3.13 (PEP 594). `shell.py`
already has a fallback to the `telnetlib3` PyPI package, but upstream's
`requirements.txt` never lists it, so the fallback itself is unmet on any
Python >= 3.13. Independently confirmed upstream: GitHub repo
`brabels/routersploitfixer` ("Compatibility patch for RouterSploit on Python
3.14 + paramiko 3.x") exists for the same class of problem.

**2. `pkg_resources` missing — only visible against the venv's own interpreter:**

```
routersploit/resources/wordlists/__init__.py:1: import pkg_resources
ModuleNotFoundError: No module named 'pkg_resources'
```

A first pass at this check ran `python3 -c "import routersploit.interpreter"`
against the **system** `python3`, with the venv's site-packages manually
prepended — and it passed, because the system interpreter already carries
`pkg_resources` via apt's `python3-setuptools`. That masked the gap: a bare
`python3 -m venv` carries no setuptools at all (pip stopped bundling it into
new venvs), and even installing `setuptools` fresh wasn't enough — setuptools
>= 81 dropped `pkg_resources` entirely (its own deprecation warning names 81
as the cutoff: "slated for removal as early as 2025-11-30 ... pin to
Setuptools<81"). `install.sh` installs `"setuptools<81"` to restore it. The
fix only surfaced once the check ran against `$VENV/bin/python3` directly, cd'd
into `$WORKDIR` — exactly what `install.sh --verify` and the final startup
check both do now.

## Verified live (this ticket, not a scratch clone)

```
$ /home/lumen/bin/rsf -h
/home/lumen/bin/routersploit/rsf.py -m <module> -s "<option> <value>"
$ ops/wq3388-routersploit/install.sh --verify
VERIFY OK
```

(`routersploit/interpreter.py:274-293` is the non-interactive `-h` path that
prints this and exits 0 — the nonInteractive handler rsf.py's `__main__`
dispatches to when argv has extra args.) Plain `python3 rsf.py` with no args
(the interactive path, `interpreter.py:112-117`) also runs clean in a non-tty
shell: it detects `not sys.stdin.isatty()`, prints `stdin is not a TTY...`,
and exits — documented behavior for running outside a real terminal, not a
crash.

Not done: `/home/lumen/bin/.gitignore` doesn't yet have a `/routersploit`
entry alongside its existing `/trivy` / `/gatus` / `/renode-dist` "large
downloaded binaries — not source" block — `lumen-bin` is a separate repo from
this one with its own PR process, out of scope for this branch.
