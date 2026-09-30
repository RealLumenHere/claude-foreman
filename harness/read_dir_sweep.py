#!/usr/bin/env python3
"""read_dir_sweep.py — wq#1441 (research/BROKER_GRANT_COVERAGE.md §2.1).

Deterministic secret sweep of the directories fleet-exec-policy.yaml `allow.read_dirs`
grants as `needs:dir(...)` read context. A read grant is the worker's `--add-dir`, i.e.
every file under the root is readable by the worker's own uid, so the question a root
has to answer before it goes on the allow list is "is there a credential anywhere in
here?" — file:line, not a feeling. wq#1466 is the counter-example this exists for:
`dir(/home/lumen/sentinel/config)` was auto-granted and the dir carried 38 literal
bearer tokens.

Regex + basename globs only, no LLM. Every hit is printed MASKED (the matched token is
cut to its first 4 characters before anything is written to stdout) — a credential
printed is a credential burned (instincts tier 0 MASK BEFORE PRINT).

    python3 harness/read_dir_sweep.py <root> [<root>...]      # human table, exit 1 on hits
    python3 harness/read_dir_sweep.py --policy [--json]       # every allow.read_dirs root
"""
import datetime
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
POLICY_PATH = os.environ.get("FOREMAN_FLEET_EXEC_POLICY",
                             os.path.join(HARNESS_DIR, "fleet-exec-policy.yaml"))
RUNTIME_PATH = os.environ.get("FOREMAN_READ_DIR_SWEEP_RUNTIME",
                              os.path.join(HARNESS_DIR, "runtime", "read_dir_sweep.json"))
AQ_BIN = os.environ.get("FOREMAN_AQ_BIN", "/home/lumen/bin/aq")
AQ_SOURCE = "read-dir-sweep"

# file names that are a credential by convention regardless of content
BASENAME_GLOBS = ("*.env", ".env*", "*.token", "*.key", "*.pem", "id_*", "*.p12", "*.pfx",
                  "*.kdbx", ".netrc", ".git-credentials", "*.secret", "*.secrets")
# wq#2114: these globs matched the canonical name and nothing else, so every BACKUP of a
# credential file was invisible -- `.git-credentials.bak.20260830_124941_pre_rotate_finish`
# matched no glob at all. That is not an edge case: `.bak.<TS>_pre_<reason>` is the fleet's
# universal convention before any risky edit (sentinel/config alone carries 24 copies of
# one config), so the convention that exists to make changes safe was quietly producing
# credential copies no basename rule could see. Strip a trailing backup suffix and re-test.
BACKUP_SUFFIX = re.compile(
    r"(?:\.bak[-.][^/]*|\.bak|~|\.orig|\.save|\.old|\.dpkg-(?:old|dist|new)|\.rpmsave)$",
    re.IGNORECASE)


def basename_candidates(name):
    """The name itself, plus what it is a backup OF. Applied repeatedly so a doubly
    suffixed copy (`id_ed25519.bak.20260101.orig`) still resolves to its origin."""
    seen = [name]
    cur = name
    for _ in range(4):
        stripped = BACKUP_SUFFIX.sub("", cur)
        if stripped == cur or not stripped:
            break
        seen.append(stripped)
        cur = stripped
    return seen
# wq#2115: the label half of `kv_secret` is shape-based, not an enumerated keyword list.
# A human pasting a credential dump labels it however the source service's docs print it —
# `Signing Secret:`, `Verifaction Token:` (sic) — so any `<word> secret|token|key` counts,
# and the words may be separated by a SPACE, not only [_-]. An exact list missed 3 live
# credentials on the same line it caught one on (messenger leak, wq#1483).
KV_NOUN = r"(?:secret|token|key|credentials?|passphrase|password|passwd)"
# one qualifier word, ended by a separator OR a camelCase hump (`client_`, `Signing `,
# `client` of `clientSecret`) — the hump test is case-SENSITIVE inside the (?i) pattern,
# which is what keeps `monkey:`/`hockey:` out while letting `apiKey:` in. Two letters
# minimum: a one-letter qualifier is an English article, and `a key: …`/`not a token: …`
# prose was 79 of the 93 lines this rule newly matched across the worker add-dirs.
# wq#2806: the hump wants a Capitalised word behind it, or an ALL_CAPS label splits
# anywhere (`PUBL` + `IC_` + `KEY` slipped past KV_NOT_PUBLIC); see harness/masking.py.
# wq#2806: one-letter segments are fine behind `_`/`-` (`X_API_KEY`); the two-letter
# minimum only guards the SPACE separator, where a lone letter is an English article.
# wq#2806 (perf, second pass): every alternative ends at a point the text FIXES — the
# first `_`/`-`, the first space, or the first capital — so a word has one reading and
# the `{0,7}` nesting cannot multiply. The earlier hump form `[A-Za-z0-9]{1,20}(?=[A-Z][a-z])`
# could end at ANY capital inside a mixed-case run, and a 554 KB base64 blob in a
# Commander log line (humps every few characters) took the rule past 8 s per window
# and stalled the sweep. Hump words are camelCase segments: `api`, `Key`, `client`,
# `OA`+`uth`, `API`; never a bare `Key` at the start (a bare noun stays _KV_BARE only).
KV_WORD = r"(?:[A-Za-z][A-Za-z0-9]{0,20}[_-]{1,2}|[A-Za-z][A-Za-z0-9]{1,20} {1,2}|(?-i:(?:[A-Z][a-z0-9]+|[A-Z]{2,3}[a-z0-9]*|[a-z][a-z0-9]+)(?=[A-Z][a-z])))"
# a public key is not a credential, and infra dirs are full of them
KV_NOT_PUBLIC = r"(?!pub(?:lic)?[ _-]{0,2}key\b)"
# bare `key` is deliberately absent here: `key:` alone is ordinary YAML/JSON, it needs a
# qualifier word in front of it (`api key`, `signing key`) to read as a credential label.
KV_BARE = r"(?:secret|token|credentials?|passphrase|password|passwd|bearer)"
# wq#2806: up to eight qualifiers — `\b` cannot open mid-identifier, so a two-deep cap
# made `LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY` invisible (harness/masking.py).
KV_LABEL = (r"(?:" + KV_WORD + r"){0,7}" + KV_NOT_PUBLIC + KV_WORD + KV_NOUN + r"|" + KV_BARE)
# an env-var reference or a path to a key file is a POINTER to a credential, not one:
# `secret = process.env.X`, `master key: /home/lumen/.ssh/id_ed25519`. The key file
# itself is caught by BASENAME_GLOBS where it lives.
KV_NOT_A_CREDENTIAL = (r"(?!os\.environ)(?!os\.getenv)(?!(?:process\.)?env\.)"
                       r"(?!\$\{?[A-Z_])(?![~.]{0,2}/)(?!<)"
                       # wq#2122: allowing a quoted label opened a new pointer shape --
                       # `{"x-internal-key": ` + `BILLING_INTERNAL_KEY}` and
                       # `{"env_key": "` + `AZURE_OPENAI_API_KEY"}`, where the value is a
                       # constant or the NAME of an env var, not its value. Both were
                       # false positives in the first --policy run of this change.
                       # Requires all-caps AND an underscore AND no lowercase, so a
                       # hex/base64 credential (which carries mixed case or no
                       # underscore) is still caught.
                       # terminator, not end-of-line: the pointer is usually mid-line
                       # (`{"x-internal-key": ` + `BILLING_INTERNAL_KEY}, timeout=3)`). Using a
                       # terminator also means a credential that merely STARTS with an
                       # all-caps run (`AAAA_BBBB-ccc123`) is still caught, because `-` is
                       # not a terminator.
                       r"(?![A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+(?:[\"'`,;\s)}\]]|$))"
                       # wq#2993: a value whose body run ENDS at the fleet's own mask
                       # literal (`token = ghp_` + `MASKED`) is mask output read back,
                       # never a live secret — same exception masking.py keys on MASK.
                       # `(?-i:)` because kv_secret compiles IGNORECASE and the literal
                       # is exact upper-case; a body that merely CONTAINS it
                       # (`xMASKEDy...`) is still caught.
                       r"(?-i:(?![A-Za-z0-9_\-./+]*MASKED(?![A-Za-z0-9_\-./+])))")
# wq#2122: the label used to have to touch its separator, so the single most common
# credential shape in the fleet -- standard JSON -- was invisible: in `"api_key": "<val>"`
# the closing quote sits between the label and the colon. Same for a markdown-bold label
# (`*Client Secret*:`, how Slack prints one) and a backtick-wrapped value.
KV_LABEL_CLOSE = r"['\"*`]?"
# `Signing Secret is: <val>` -- a connector word between label and colon. Enumerated, not
# `\w+`, so an arbitrary sentence cannot bridge a prose `secret` to an unrelated long token.
KV_CONNECTOR = r"(?:\s+(?:is|was|=))?"
# `Signing Secret - <val>`. The dash REQUIRES surrounding whitespace: a bare `-` would make
# `api-key-rotation-procedure-doc` read as label `api-key`, separator `-`, value the rest.
KV_SEP = r"(?:\s*[:=]\s*|\s+[-\u2013]\s+)"
# content shapes; first match per line wins. `kv_secret` needs a value of 20+ chars so
# `api_key: os.environ/X` (LiteLLM's env-ref form) and `api_key: sk-none` do not fire.
# wq#2993: a body run that ENDS at the fleet's own mask literal is mask output read
# back (`ghp_` + 16 quoted chars + `MASKED` in a mask_at_rest context window), never a
# live token — masking.py keys the same exception on MASK. Interpolated into each
# prefixed pattern with that pattern's own body class; tail-only and case-sensitive,
# so `ghp_MASKEDx…` (literal mid-run) and a lower-case `masked` still page.
def _not_mask_tail(body_class):
    return r"(?!" + body_class + r"*MASKED(?!" + body_class + r"))"


PATTERNS = (
    # wq#2114: ghr_/ghu_ were absent here but present in the token-engine broker's copy —
    # the three scanners disagreed on which GitHub prefixes exist. Widened to the union.
    ("gh_pat", re.compile(
        r"\b(?:ghp_|github_pat_|gho_|ghs_|ghr_|ghu_|glpat-)"
        + _not_mask_tail(r"[A-Za-z0-9_-]") + r"[A-Za-z0-9_-]{20,}")),
    ("sntl", re.compile(r"\bsntl-p" + _not_mask_tail(r"[A-Za-z0-9_-]")
                        + r"[A-Za-z0-9_-]{8,}")),
    ("sk_key", re.compile(r"\bsk-(?:proj-|ant-|or-v1-|lf-)?"
                          + _not_mask_tail(r"[A-Za-z0-9_-]") + r"[A-Za-z0-9_-]{24,}")),
    ("aws", re.compile(r"\bAKIA(?![0-9A-Z]{10}MASKED\b)[0-9A-Z]{16}\b")),
    ("slack", re.compile(r"\b(?:xox[bpsa]|xapp)-" + _not_mask_tail(r"[A-Za-z0-9-]")
                         + r"[A-Za-z0-9-]{20,}")),
    ("privkey", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY")),
    # wq#2479: a credential embedded in a URL, scheme://user:secret@host. No label sits in
    # front of the value, so kv_secret cannot see it (its label half never engages) and none
    # of the other patterns has a URL shape. The match is the WHOLE `scheme://user:secret@`
    # span: mask() keeps 4 chars and the scheme plus `://` is already 5+, so no character of
    # the userinfo can reach the excerpt. Not a credential: an empty password, a template or
    # env-ref (`${T}`, `$T`, `{pw}`, `%s`, `<pw>`, `os.environ`, ALL_CAPS_NAME) and the
    # literal placeholder words docs use. `secret{8,}` keeps `user:pw@` and `a:b@` out.
    ("url_basic_auth", re.compile(
        r"\b[A-Za-z][A-Za-z0-9+.\-]+://[^\s:/@'\"`]{1,64}:"
        r"(?![${%<*.]|(?i:password|passwd|secret|changeme|example|redacted|masked|xxxx+)@)"
        # wq#2993: a password ENDING in the fleet's mask literal is mask output read
        # back (`ghp_MASKED@`, the kept prefix glued to MASK) — the placeholder test
        # above only sees a value that IS the word. Case-sensitive on purpose, same
        # reasoning as masking.py's MASK exception: nothing legitimate carries
        # upper-case MASKED as the tail of a secret body.
        r"(?![^\s@/'\"`]*MASKED@)"
        r"(?!os\.environ|os\.getenv|(?:process\.)?env\.)"
        r"(?![A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+@)"
        r"[^\s@/'\"`]{8,}@(?=[A-Za-z0-9\[])")),
    ("kv_secret", re.compile(
        r"(?i)\b(?:" + KV_LABEL + r")\b" + KV_LABEL_CLOSE + KV_CONNECTOR + KV_SEP +
        r"['\"`]?" + KV_NOT_A_CREDENTIAL +
        r"[A-Za-z0-9_\-./+]{20,}")),
)
# wq#3203: .pytest_cache is tool output, not readable content a grant exposes on top of
# what the repo already carries — and its v/cache/nodeids (every pytest node id, one
# string) read as sk_key hits 215 times in the first full foreman sweep, flooding aq.
SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv",
                       "site-packages", ".cache", ".pytest_cache"})
# wq#3203: bulk snapshot archives, deferred to the END of each root's walk under their
# own budget. sorted() put `_runs` (0x5F < 'a') ahead of every lowercase sibling, so
# foreman's 42k-file snapshot archive ate the whole ROOT_TIMEOUT_S and events/ ops/
# reports/ harness/ … (~9.9k source files) were never reached — silent coverage loss
# in a security scan, visible only as a bare `partial=true`.
BULK_DIRS = frozenset({"_runs"})
MAX_FILE_BYTES = 2_000_000
MASK_KEEP = 4
# wq#3167: the 09-29 05:16Z run timed out at TimeoutStartSec=600s (9m57s CPU) the first
# night after wq#2119 folded agents.yaml `defaults.add_dirs` into the swept roots — the
# 31 `allow.read_dirs` policy roots alone still finish in ~23s (verified: last write
# before wq#2119 landed, runtime/read_dir_sweep.json ts=2026-09-28T05:18:17Z, 5422 files
# total), but /home/lumen/foreman is one of the 7 new add_dirs roots and carries a
# _runs/ snapshot archive (40797 files measured 2026-09-29, 77% of foreman's tree) that
# a single unbounded os.walk had no way to bound. A per-root cap keeps one oversized or
# pathological root (regex cost scales with content, not just file count — wq#2806) from
# ever eating the whole run; a global cap guarantees write_runtime() always writes a
# FRESH runtime/read_dir_sweep.json before systemd's TimeoutStartSec fires, which is what
# capability_lint.sweep_stale() actually gates on — a truncated LATE root only loses that
# root's coverage for one day, where a timed-out PROCESS loses every root's freshness and
# fails every dir() grant closed (capability_lint.py:673-676). Policy read_dirs roots are
# still swept first (write_runtime's `roots` order below), so the roots the dir() gate
# reads (sweep_hit_under) are the ones least likely to ever be truncated.
# wq#3203: 90s starved foreman's source dirs even after BULK_DIRS deferral. Measured
# 2026-09-30: foreman's source (non-BULK_DIRS) phase is 12,223 files — 33.5s warm-cache
# on this box, but the cold 05:21Z systemd run sustained only ~107 files/s (9,678 files
# in 90.03s), which puts the same phase at ~115s worst case. 150s covers that with ~30%
# headroom; the sum of ALL per-root elapsed in the 05:21Z run was 175s against the 480s
# global budget, so one root at 150+45 still leaves >280s for the other 37 roots,
# and SWEEP_DEADLINE_S stays well under systemd's TimeoutStartSec=600.
ROOT_TIMEOUT_S = float(os.environ.get("FOREMAN_READ_DIR_SWEEP_ROOT_TIMEOUT_S", "150"))
# wq#3203: the bulk (BULK_DIRS) phase's own per-root budget, ON TOP of ROOT_TIMEOUT_S.
# foreman/_runs alone (42k files) would need ~390s to finish, so it stays partial by
# design; the budget buys ~5k snapshot files a day without ever starving a sibling.
BULK_TIMEOUT_S = float(os.environ.get("FOREMAN_READ_DIR_SWEEP_BULK_TIMEOUT_S", "45"))
SWEEP_DEADLINE_S = float(os.environ.get("FOREMAN_READ_DIR_SWEEP_DEADLINE_S", "480"))


def mask(token):
    return token[:MASK_KEEP] + "…" if len(token) > MASK_KEEP else "…"


# wq#2120: this sweep had NO false-positive mechanism at all — wq523_pat_residue.py grew
# its own fingerprint ack list post-wq1642, but a `dir()` grant here has failed closed on
# ANY hit under the cited root since wq#1482, with no way to clear a hit once a human has
# actually read it and confirmed it isn't a credential. wq#2115's broadened kv_secret shape
# turned up 3 such hits (none live) that now permanently block every dir() grant under
# their two roots. Fingerprinted on the exact matched span (sha256[:12], same convention as
# FALSE_POSITIVE_FINGERPRINTS in wq523_pat_residue.py) rather than by path, so a genuinely
# new secret landing on the same line or file still fires fresh. Values are DELIBERATELY not
# reproduced in this comment: this file scans read-grant roots for exactly this shape, and a
# literal here would make it flag its own source.
REVIEWED_HIT_FINGERPRINTS = {
    # research/harnessrouter-ref/src/docs/self-hosting-guide.md:118 — HR_AUTH_PASSWORD=
    # placeholder in the upstream self-hosting doc mirror; hyphenated English words, zero
    # digits, sits in a bash example telling the reader what to substitute in
    "527602abdce7",
    # research/harnessrouter-ref/src/docs/self-hosting-guide.md:541 — HR_SECRET_KEY=,
    # same doc, same placeholder shape
    "aea20d56e205",
    # research_prompts/.scratch/autorandr_Makefile:71 — `desktop-file-edit --remove-key=`,
    # a real label/value CLI-flag shape, not a secret
    "ba3880ebd497",
    # wq#3165: minerva/dream/prose_restore/014_current-state-overrides-...md:7 —
    # `LITELLM_MASTER_KEY=<placeholder>` prose describing where the key lives (a
    # claude_memory.md CORE section pre-image copied verbatim by
    # lumen_prose_lifecycle.py). Confirmed not live: 0 digits, 0 uppercase, dash-
    # joined lowercase ending in the English word "master"; does not equal or
    # contain any current /home/lumen/agent/.env value, including
    # LITELLM_MASTER_KEY's own. This was blocking lumen-minerva-ref-sync since
    # 09-28 (aq#2353).
    "fdc0aa8d5758",
    # wq#3117: logs/mega1-verify.log:36 — a mask-verify transcript quoting a code sample,
    # `token =` then a PascalCase function name that is being CALLED (the next char is
    # `(`). A call expression, not a value: no digits, no key-shaped run, and the log is a
    # test transcript. This one line failed every dir(/home/lumen/logs) grant closed, so
    # the guardian's fire tickets (which cite that dir) could not be dispatched. Pinned on
    # the exact span; a real secret landing on the same line still fires fresh. Class
    # (kv_secret flagging identifier-then-paren) is tracked separately.
    "f2cd95469dbb",
}


def reviewed_hit_fingerprint(matched_text):
    """sha256(matched_text)[:12] — the identity a reviewed-hit ack is keyed on. Never
    call this on anything before it's been reviewed by a human; it exists to let a
    CONFIRMED non-secret stop re-blocking dir() grants, not to auto-clear new hits."""
    return hashlib.sha256(matched_text.encode()).hexdigest()[:12]


# wq#2993: a mask FINDING is a report ABOUT a credential, not a credential. mask_at_rest
# writes one JSONL row per value it masked (_runs/mask_at_rest/<ts>.jsonl) and the eval
# gate keeps the same rows one level down (_runs/<ticket>/mask-hits-<n>.json). The value
# in a row is already hash + MASKED, but the row keeps a verbatim `context` window for
# triage, and what that window quotes — test_masking.py parametrize fixture literals
# above all — reads as a credential to every content pattern here (119 of foreman's 390
# hits were ONE mask_at_rest log; 33 more the mask-hits copies). Same design call as
# wq#2120's fixture-fingerprint allowlist: the fleet's own report artifacts, pinned by
# PATH SHAPE (exactly one level under a `_runs` component), are exempt from content
# patterns only — BASENAME_GLOBS still apply, and the same bytes copied anywhere else
# still page, which is what keeps the exemption falsifiable.
def is_mask_report(path):
    parts = os.path.normpath(path).split(os.sep)
    if "_runs" not in parts[:-1]:
        return False
    rel = parts[parts.index("_runs") + 1:]
    if len(rel) != 2:
        return False
    if rel[0] == "mask_at_rest" and rel[1].endswith(".jsonl"):
        return True
    return fnmatch.fnmatch(rel[1], "mask-hits-*.json")


def _read_text(path):
    try:
        if os.path.islink(path) or os.path.getsize(path) > MAX_FILE_BYTES:
            return None
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None
    return data.decode("utf-8", "replace")


def sweep_root(root):
    """[(path, line, kind, masked_excerpt)] for one root. Deterministic order. Thin,
    unbounded wrapper over `_sweep_root_bounded` (wq#3167) kept at this 2-tuple shape
    since it's called directly all over test_read_dir_sweep.py; `_sweep_report` is the
    only caller that needs the deadline/partial-flag behavior, via the bounded form."""
    hits, nfiles, _partial, _not_reached = _sweep_root_bounded(root, deadline=None)
    return hits, nfiles


def _scan_file(path, name, hits):
    """Scan one file against BASENAME_GLOBS and PATTERNS, appending to `hits`."""
    if any(fnmatch.fnmatch(c, g)
           for c in basename_candidates(name) for g in BASENAME_GLOBS):
        hits.append((path, 0, "basename", name))
    if is_mask_report(path):
        return
    text = _read_text(path)
    if text is None:
        return
    for i, line in enumerate(text.splitlines(), 1):
        for kind, rx in PATTERNS:
            m = rx.search(line)
            if m:
                if reviewed_hit_fingerprint(m.group(0)) in REVIEWED_HIT_FINGERPRINTS:
                    break  # wq#2120: reviewed non-secret, not a live hit
                # mask BEFORE truncating: a cut token would escape the replace
                masked = line.replace(m.group(0), mask(m.group(0)))
                hits.append((path, i, kind, masked.strip()[:120]))
                break


def _walk_subtree(start, deadline, hits):
    """(file count, truncated flag) for one subtree, same sorted walk order as before
    wq#3203, deadline checked once per file (cheap relative to the per-line regex work
    it bounds)."""
    nfiles = 0
    for dp, dn, fn in os.walk(start):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS)
        for f in sorted(fn):
            if deadline is not None and time.monotonic() >= deadline:
                return nfiles, True
            nfiles += 1
            _scan_file(os.path.join(dp, f), f, hits)
    return nfiles, False


def _sweep_root_bounded(root, deadline=None, bulk_deadline=None):
    """[(path, line, kind, masked_excerpt)], file count, partial flag, not_reached list.

    wq#3167 bounded the walk with a single absolute time.monotonic() `deadline`; wq#3203
    splits it into two phases because os.walk's sorted() order put `_runs` (0x5F) ahead
    of every lowercase sibling, so a bulk snapshot archive spent the whole budget and the
    actual source dirs were never reached. Order now: root-level files, then every
    top-level dir NOT in BULK_DIRS (sorted, each subtree fully, bounded by `deadline`),
    then BULK_DIRS members (bounded by `bulk_deadline`, defaulting to `deadline`) — a
    bulk dir can only ever truncate itself. `not_reached` lists top-level dirs whose
    walk never STARTED, so a partial root says WHAT was cut, not just that something
    was (a dir that started but was truncated mid-walk is covered by `partial`)."""
    if bulk_deadline is None:
        bulk_deadline = deadline
    hits = []
    nfiles = 0
    partial = False
    not_reached = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        entries = []
    root_files, source_dirs, bulk_dirs = [], [], []
    for e in entries:
        p = os.path.join(root, e)
        if os.path.isdir(p):
            # symlinked dirs: os.walk(followlinks=False) never descended them, keep that
            if e in SKIP_DIRS or os.path.islink(p):
                continue
            (bulk_dirs if e in BULK_DIRS else source_dirs).append(e)
        else:
            root_files.append(e)
    for f in root_files:
        if deadline is not None and time.monotonic() >= deadline:
            partial = True
            break
        nfiles += 1
        _scan_file(os.path.join(root, f), f, hits)

    def _phase(dirs, phase_deadline):
        nonlocal nfiles, partial
        for i, d in enumerate(dirs):
            if phase_deadline is not None and time.monotonic() >= phase_deadline:
                partial = True
                not_reached.extend(dirs[i:])
                return
            n, truncated = _walk_subtree(os.path.join(root, d), phase_deadline, hits)
            nfiles += n
            if truncated:
                partial = True
                not_reached.extend(dirs[i + 1:])
                return

    if partial:  # root-file loop already hit the deadline: no source dir ever starts
        not_reached.extend(source_dirs)
    else:
        _phase(source_dirs, deadline)
    # bulk phase runs regardless: its budget is its own, a starved source phase must
    # not ALSO forfeit the bulk one (and vice versa — that asymmetry was the bug)
    _phase(bulk_dirs, bulk_deadline)
    return hits, nfiles, partial, not_reached


def policy_read_dirs(path=POLICY_PATH):
    import yaml
    with open(path) as f:
        data = yaml.safe_load(f.read()) or {}
    return list(((data.get("allow") or {}).get("read_dirs")) or [])


AGENTS_SPEC_PATH = os.environ.get("FOREMAN_AGENTS_SPEC",
                                  os.path.join(HARNESS_DIR, "agents.yaml"))


def worker_add_dirs(path=AGENTS_SPEC_PATH):
    """wq#2119: `allow.read_dirs` above is the fleet-exec BROKER grant list and was the
    only thing this sweep covered — but every worker also gets `defaults.add_dirs` from
    agents.yaml unconditionally (agents_spec.py / fworker.py WORKER_ADD_DIRS), a second,
    separate read-grant surface this sweep never touched. That's how two credential-shaped
    lines sat unswept in /home/lumen/agents/work-queue/_runs (a default add_dir) until a
    manual audit found them. Folded into write_runtime()'s default roots below so the
    daily sweep covers both grant mechanisms, not just the broker one."""
    import yaml
    with open(path) as f:
        data = yaml.safe_load(f.read()) or {}
    return list((data.get("defaults") or {}).get("add_dirs") or [])


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sweep_report(roots, deadline_s=None):
    """`deadline_s` (wq#3167), when given, is a wall-clock budget for the WHOLE call,
    checked before each root starts, plus each root gets its own ROOT_TIMEOUT_S share
    for source dirs and BULK_TIMEOUT_S on top for BULK_DIRS (wq#3203; both capped to
    whatever's left of the global budget) — so one oversized or pathological
    root (wq#2806: regex cost scales with content, not file count) can truncate itself
    without ever blocking a later root from starting, and the global budget guarantees
    the caller (write_runtime) always gets a report back instead of running past
    systemd's TimeoutStartSec. `roots` order matters: a root skipped or truncated by the
    deadline is always the LAST one processed, never an earlier one (interactive/--policy
    callers pass deadline_s=None and are unaffected — unbounded, as before)."""
    report = {}
    start = time.monotonic()
    global_deadline = start + deadline_s if deadline_s is not None else None
    for root in roots:
        if global_deadline is not None and time.monotonic() >= global_deadline:
            report[root] = {"error": "skipped — sweep deadline reached before this "
                                     "root started (wq#3167)", "files": 0, "hits": []}
            continue
        if not os.path.isdir(root):
            report[root] = {"error": "not a directory", "files": 0, "hits": []}
            continue
        root_deadline = time.monotonic() + ROOT_TIMEOUT_S
        # wq#3203: BULK_DIRS get their own budget ON TOP of the source one, so a bulk
        # archive can never starve source dirs and a slow source phase never forfeits
        # the bulk phase; both instants stay capped by the global deadline.
        bulk_deadline = root_deadline + BULK_TIMEOUT_S
        if global_deadline is not None:
            root_deadline = min(root_deadline, global_deadline)
            bulk_deadline = min(bulk_deadline, global_deadline)
        t0 = time.monotonic()
        hits, nfiles, partial, not_reached = _sweep_root_bounded(
            root, deadline=root_deadline, bulk_deadline=bulk_deadline)
        entry = {"files": nfiles, "elapsed_s": round(time.monotonic() - t0, 2), "hits": [
            {"path": p, "line": ln, "kind": k, "excerpt": ex} for p, ln, k, ex in hits]}
        if partial:
            entry["partial"] = True
        if not_reached:
            entry["not_reached"] = not_reached
        report[root] = entry
    return report


def _prev_hit_keys(prev_roots):
    keys = set()
    for root, r in (prev_roots or {}).items():
        for h in r.get("hits") or []:
            keys.add((root, h.get("path"), h.get("line"), h.get("kind")))
    return keys


def write_runtime(roots=None, runtime_path=None, deadline_s=None):
    """Sweep every `allow.read_dirs` root plus every `defaults.add_dirs` root (or
    `roots`), write the masked report to `runtime_path` (wq#1482:
    lumen-read-dir-sweep.timer's daily payload), and return `(report, new_hits)`.
    `new_hits` are hits whose (root, path, line, kind) was not present in the PREVIOUS
    runtime write, so a rerun of an already-known hit does not re-alert every day —
    only a hit that is new since the last sweep does. `deadline_s` (wq#3167) defaults
    to SWEEP_DEADLINE_S: this call always finishes and WRITES a fresh file well inside
    systemd's TimeoutStartSec, because capability_lint.sweep_stale() fails every dir()
    grant closed off the file's top-level `ts` the moment the whole process fails to
    complete — a root truncated by the deadline only loses ITS OWN coverage for a day;
    a run that never finishes loses every root's freshness at once. `policy_read_dirs()`
    roots are placed first in the default `roots` order (below), same as before, so the
    roots capability_lint's dir() gate actually reads (sweep_hit_under) are the ones
    least likely to ever hit the deadline."""
    runtime_path = runtime_path or RUNTIME_PATH
    if deadline_s is None:
        deadline_s = SWEEP_DEADLINE_S
    if roots is not None:
        roots = list(roots)
    else:
        # wq#2119: add_dirs folded in alongside read_dirs — see worker_add_dirs().
        seen = []
        for r in policy_read_dirs() + worker_add_dirs():
            if r not in seen:
                seen.append(r)
        roots = seen
    prev = None
    try:
        with open(runtime_path) as f:
            prev = json.load(f)
    except Exception:
        prev = None
    prev_keys = _prev_hit_keys((prev or {}).get("roots"))
    report = _sweep_report(roots, deadline_s=deadline_s)
    new_hits = []
    for root, r in report.items():
        for h in r.get("hits") or []:
            key = (root, h.get("path"), h.get("line"), h.get("kind"))
            if key not in prev_keys:
                new_hits.append((root, h))
    os.makedirs(os.path.dirname(runtime_path), exist_ok=True)
    with open(runtime_path, "w") as f:
        json.dump({"ts": _now_iso(), "roots": report}, f, indent=1, sort_keys=True)
    return report, new_hits


def aq_alert(new_hits, aq_bin=None):
    """aq add one row per NEW masked hit (source=read-dir-sweep). Fail-open: no aq
    binary or a failed call must never break the sweep itself (instinct: mask before
    print — the excerpt handed to aq is already masked by sweep_root)."""
    aq_bin = aq_bin or AQ_BIN
    if not new_hits or not os.path.exists(aq_bin):
        return
    for root, h in new_hits:
        title = f"read_dir_sweep: fresh hit under {root}"
        body = f"{h.get('path')}:{h.get('line')} [{h.get('kind')}] {h.get('excerpt')}"
        key = f"{root}:{h.get('path')}:{h.get('line')}"
        try:
            subprocess.run([aq_bin, "add", AQ_SOURCE, key, title, body],
                           check=False, timeout=15)
        except Exception:
            pass


def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="secret sweep of read-grant roots (masked output)")
    ap.add_argument("roots", nargs="*")
    ap.add_argument("--policy", action="store_true", help="sweep every allow.read_dirs root")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-lines", type=int, default=40, help="hits shown per root (text mode)")
    ap.add_argument("--write-runtime", action="store_true",
                    help="sweep every allow.read_dirs root, write runtime/read_dir_sweep.json, "
                         "aq-alert on any hit new since the last write (wq#1482)")
    a = ap.parse_args(argv)
    if a.write_runtime:
        report, new_hits = write_runtime()
        aq_alert(new_hits)
        total = sum(len(r.get("hits") or []) for r in report.values())
        # wq#3167: a root that hit ROOT_TIMEOUT_S/SWEEP_DEADLINE_S still gets a hits
        # list (possibly partial) in `report`; one skipped before it ever started
        # carries "error" instead — surface both so a slow/growing root is visible in
        # the unit's log, not just silently thinner than the day before.
        incomplete = [root for root, r in report.items() if r.get("partial") or "error" in r]
        incomplete_note = f", {len(incomplete)} incomplete: {incomplete}" if incomplete else ""
        print(f"read_dir_sweep: wrote {RUNTIME_PATH} — {total} hits, {len(new_hits)} new"
              f"{incomplete_note}")
        # wq#3203: a bare `partial` hid WHICH dirs a truncated root never got to —
        # say it in the unit log, per root, so lost coverage is visible in journalctl
        for root in incomplete:
            nr = report[root].get("not_reached")
            if nr:
                print(f"read_dir_sweep: {root} partial — not reached: {', '.join(nr)}")
        # wq#2115: a FINDING is not a FAILURE. This mode runs as a systemd oneshot, and
        # a hit-driven exit 1 marks lumen-read-dir-sweep.service failed and skips its
        # ExecStartPost cron stamp — i.e. a found secret looks exactly like a broken
        # sweep. The finding channel is the runtime payload + aq; keep the exit code for
        # the sweep genuinely not running. Interactive/CI mode below still exits 1.
        return 0
    roots = list(a.roots)
    if a.policy:
        roots += policy_read_dirs()
    if not roots:
        ap.error("give at least one root or --policy")
    report = _sweep_report(roots)
    total = sum(1 if "error" in r else len(r.get("hits") or []) for r in report.values())
    if a.json:
        print(json.dumps(report, indent=1, sort_keys=True))
    else:
        for root, r in report.items():
            if "error" in r:
                print(f"{root}: {r['error']}")
                continue
            print(f"{root}: files={r['files']} hits={len(r['hits'])}")
            for h in r["hits"][:a.max_lines]:
                print(f"  {h['path']}:{h['line']} [{h['kind']}] {h['excerpt']}")
            if len(r["hits"]) > a.max_lines:
                print(f"  … {len(r['hits']) - a.max_lines} more")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(_main())
