"""wq#1193 — the Messenger end of Lumen's cross-platform persona memory.

wq#1176 item 4: "persona facts flow both ways". A fact Lumen states on stream
has to be canonical here on the next Messenger turn, and a fact he states here
has to be canonical on the stream. The shared thing is lumen_state (the fleet's
bi-temporal store), subject `lumen-persona`, one predicate per slot — written by
LPP's persona.ts (wq#1191) and read here. There is no second copy and no sync
step to forget to run.

The two directions are deliberately NOT symmetric, because they are different
kinds of problem:

  READ (recall_block) is a STORE-FORMAT problem. It reads the open rows
  natively — importing lumen_state.py and calling current() — so a Messenger
  turn costs one sqlite query, not a node process, and Messenger keeps working
  whether or not LPP is checked out or built. The predicate encoding it decodes
  (`key__member` for set-valued slots) is the store's own on-disk contract,
  documented in LPP src/persona_store.ts; the CLI's human render is NOT parsed,
  because a value containing " = " makes that ambiguous.

  WRITE (note_reply) is a POLICY problem — what counts as a durable self-fact,
  when a restatement is a change of mind versus a contradiction to refuse. That
  policy is persona.ts and it exists exactly once, so this shells out to LPP's
  cross-platform CLI (`persona_cli.ts state --platform messenger`) rather than
  growing a second, drifting extractor here. If LPP is unavailable the write
  degrades to a logged no-op; nothing about a Messenger turn depends on it.

Both directions fail open. A persona failure must never cost a reply.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import re
import subprocess
import threading

log = logging.getLogger("lumen_persona")

STATE_PY = os.getenv("LUMEN_STATE_PY", "/home/lumen/minerva/lumen_state.py")
SUBJECT = os.getenv("LUMEN_PERSONA_SUBJECT", "lumen-persona")
RECALL_BUDGET = int(os.getenv("LUMEN_PERSONA_RECALL_BUDGET", "900"))

# Ingest side. LPP_DIR is the cwd persona_cli.ts needs for its relative imports.
LPP_DIR = os.getenv("LUMEN_LPP_DIR", "/home/lumen/pokemon")
PERSONA_CLI = os.getenv("LUMEN_PERSONA_CLI", "src/persona_cli.ts")
# persona_cli.ts is a tsx shebang script; the runner is a setting so a probe can
# swap in a recorder and assert the argv without a node install.
INGEST_RUNNER = os.getenv("LUMEN_PERSONA_RUNNER", "npx tsx").split()
PLATFORM = os.getenv("LUMEN_PERSONA_PLATFORM", "messenger")
INGEST_TIMEOUT_S = float(os.getenv("LUMEN_PERSONA_INGEST_TIMEOUT_S", "45"))
INGEST_MAX_CHARS = int(os.getenv("LUMEN_PERSONA_INGEST_MAX_CHARS", "1200"))

# Same wording the stream side wraps recall in (LPP src/prompt.ts) — the point of
# the feature is that both surfaces put the SAME facts under the SAME framing.
HEADER = ("PERSONA (canonical - you have told people this about yourself, on "
          "this platform or another; stay consistent):")

_SET_MARK = "__"

_state_mod = None
_state_lock = threading.Lock()
_ingest_lock = threading.Lock()
_cli_missing_logged = False


# ── Read ─────────────────────────────────────────────────────────────────────

def _state():
    """lumen_state.py loaded by path, once. It is a fleet script, not an
    installed package, and this is the same load LPP's own adapter does
    (tools/lumen_state_json.py) — an encoding of one store, not a second one."""
    global _state_mod
    with _state_lock:
        if _state_mod is None:
            spec = importlib.util.spec_from_file_location("lumen_state_ro", STATE_PY)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _state_mod = mod
        return _state_mod


def _decode(predicate: str):
    """(key, single) from a stored predicate. Set-valued slots carry their member
    in the predicate (`i_like__caves`) because lumen_state holds ONE open row per
    (subject, predicate) — right for "my name", wrong for "i like", where a second
    value must not evict the first. See LPP src/persona_store.ts."""
    i = predicate.find(_SET_MARK)
    if i < 0:
        return predicate.replace("_", " "), True
    return predicate[:i].replace("_", " "), False


def facts():
    """Open persona rows as (key, value, single, valid_from), ranked for recall.

    Rank mirrors persona.ts: identity slots (single-valued) first, then the fact
    held longest — the one an audience will test him on. persona.ts also ranks by
    how often he has restated a fact; that counter is session-local to the
    streamer and is not in the store, so it has no analogue here."""
    rows = _state().current(SUBJECT)
    out = []
    for r in rows:
        key, single = _decode(r["predicate"])
        value = (r.get("value") or "").strip()
        if key and value:
            out.append((key, value, single, r.get("valid_from") or ""))
    out.sort(key=lambda f: (not f[2], f[3], f[0]))
    return out


def recall_block(budget: int = RECALL_BUDGET) -> str:
    """The persona lines for a prompt, or "" when nothing has been said yet.
    Line shape matches the stream's so the same fact reads identically on both."""
    lines, used = [], 0
    for key, value, _single, _vf in facts():
        line = f"- {key}: {value}"
        if used + len(line) + 1 > budget:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def with_persona(system: str, budget: int = RECALL_BUDGET) -> str:
    """Append the persona block to a system prompt. Fail-open: any failure, or an
    empty store, returns the prompt untouched — an empty PERSONA header would
    read as "you have no self".

    Appended, not prepended, for the same reason as the telemetry block: the
    worklog+persona head stays a stable cacheable prefix, and this block changes
    only when Lumen says something new about himself."""
    try:
        block = recall_block(budget)
    except Exception as e:
        log.warning("persona recall failed, prompt unchanged: %s", e)
        return system
    if not block:
        return system
    return f"{system}\n\n{HEADER}\n{block}"


# ── Write ────────────────────────────────────────────────────────────────────

# wq#1176 item 5 guardrails, enforced HERE rather than downstream. LPP's
# extractor is scoped to durable facts about himself, but Messenger-Lumen holds
# tools and talks about the fleet in the first person ("my gateway is at ..."),
# which the streamer never does. A sentence that reaches the persona store is a
# sentence the STREAM may later read out loud, so anything carrying infrastructure
# or a secret is refused before it leaves this process.
_UNSAFE = re.compile(
    r"""\b\d{1,3}(?:\.\d{1,3}){3}\b        # IPv4 (tailscale/LAN addresses)
      | \b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b # MAC
      | \bsk-[A-Za-z0-9_-]{8,}             # api keys
      | \bgh[pousr]_[A-Za-z0-9]{16,}
      | \b(?:token|api[_-]?key|password|passwd|secret|credential|bearer)\b
      | \b(?:tailscale|systemctl|journalctl|litellm|cloudflared|docker|sqlite|ssh)\b
      | (?:^|\s)/(?:home|etc|var|root|opt|srv)/ # filesystem paths
      | \blumen-[a-z0-9-]+\b               # fleet unit names
      | \b(?:wq|PR)#?\d+\b                 # ticket / PR refs
    """,
    re.IGNORECASE | re.VERBOSE,
)


def is_safe_to_ingest(text: str) -> bool:
    """True when a reply may be mined for self-facts. Conservative by design: a
    false negative costs one un-remembered fact, a false positive puts fleet
    infrastructure in a fictional character's mouth on a public stream."""
    return bool(text) and not _UNSAFE.search(text)


def ingest_argv(text: str, platform: str) -> list:
    """The persona_cli invocation for a reply. Split out so the contract with LPP
    is assertable without spawning anything."""
    return INGEST_RUNNER + [PERSONA_CLI, "state", text[:INGEST_MAX_CHARS],
                            "--platform", platform]


def _ingest(text: str, platform: str) -> None:
    global _cli_missing_logged
    cli = os.path.join(LPP_DIR, PERSONA_CLI)
    if not os.path.exists(cli):
        if not _cli_missing_logged:
            _cli_missing_logged = True
            log.warning("persona ingest disabled: %s not found (LPP not checked "
                        "out or wq#1191 not landed). Recall still works.", cli)
        return
    # Serialised: 'state' is a read-modify-write against the shared store, and
    # its same-day contradiction check has to see the previous turn's writes.
    with _ingest_lock:
        try:
            r = subprocess.run(
                ingest_argv(text, platform), cwd=LPP_DIR,
                capture_output=True, text=True, timeout=INGEST_TIMEOUT_S)
        except Exception as e:
            log.warning("persona ingest failed: %s", e)
            return
    if r.returncode == 3:
        # Exit 3 is the CLI refusing a same-day contradiction. That is the policy
        # working, not an error: the fact he is known for stands.
        log.info("persona ingest refused a contradiction: %s",
                 (r.stdout or "").strip()[:200])
    elif r.returncode != 0:
        log.warning("persona ingest rc=%s: %s", r.returncode,
                    (r.stderr or r.stdout or "").strip()[:200])


def note_reply(text: str, platform: str = None) -> None:
    """Mine something Lumen just said on Messenger for durable self-facts.

    Returns immediately — extraction is a model call, and it must not sit between
    Lumen's reply and the user seeing it. Off the response path there is nothing
    to report back to, so failures are logged, not raised."""
    if not is_safe_to_ingest(text):
        return
    try:
        threading.Thread(target=_ingest, args=(text, platform or PLATFORM),
                         daemon=True).start()
    except Exception as e:
        log.warning("persona ingest thread failed: %s", e)
