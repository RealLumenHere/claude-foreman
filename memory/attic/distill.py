#!/usr/bin/env python3
"""distill.py — the explicit extraction step between episodic and distilled
memory (wq#1183, pattern C of kb/fleet-architecture-ideas.md §1).

Same shape as the KB distiller (projects/lumen-kb-mcp/distill.py) and the LPP
persona extractor (wq#1176): read a raw transcript/episode, ask a gateway model
for 0-N durable facts, land them in a durable store with provenance. The store
here is git-backed markdown plus (optionally) lumen_state — deliberately NOT a
vector DB; the report's §2 says measure before you embed.

    episode (RUNS_ROOT/wq<id>/episode.jsonl, written by episodic.py via fworker)
        |
        |  this CLI — one LiteLLM call, JSON out, validated + secret-scrubbed
        v
    FLEET_MEMORY_ROOT/facts/<utc-date>-wq<id>.md   (git commit, best-effort)
        + proposed `lumen_state.py ingest` commands (run only with --ingest)

Run it as a separate pass after a run completes — never inline in a worker.
That separation is the whole point (report §1.C): one throttle point for
quality, provenance on every fact, and re-runnable distillation.

Usage:
    python3 memory/distill.py --ticket 1183
    python3 memory/distill.py --ticket 1183 --ingest     # also run state ingests

Zero-quota testing: tests monkeypatch `chat`.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request

# episodic.py stayed in memory/ when this moved to attic/ (wq#3222: it only imported when
# another test had already put memory/ on sys.path)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import episodic  # noqa: E402

GW = os.environ.get("LITELLM_URL", "http://localhost:4000/v1")
DEFAULT_STORE = os.environ.get("FLEET_MEMORY_ROOT", "/home/lumen/fleet-memory")
DEFAULT_MODEL = os.environ.get("FLEET_MEMORY_MODEL", "qwen36-local")
LUMEN_STATE_CLI = os.environ.get(
    "LUMEN_STATE_CLI", "/home/lumen/minerva/lumen_state.py")

MAX_FACTS = 5
MAX_TRANSCRIPT_CHARS = 60000
MAX_FACT_CHARS = 400

EXTRACT_SYSTEM = """You are the memory distiller for a fleet of autonomous build agents.
You are given the episode log of one completed work-queue run (JSON events: attempt
outcomes, final messages, assistant output tails).

Extract AT MOST {max_facts} durable, reusable facts a FUTURE agent on a DIFFERENT
ticket would benefit from knowing. Durable means: still true next week, not specific
to this ticket's diff. Good facts: infrastructure quirks, repo conventions, tool
gotchas, recurring failure modes and their fixes. Bad facts: what this ticket changed
(git already records that), chit-chat, restated ticket text, speculation.

NEVER include credentials, tokens, API keys, or anything that looks like a secret.
Do not invent; if the episode supports no durable fact, return an empty list.

Output ONLY a JSON array (no prose, no code fence), each element:
  {{"fact": "<one sentence>",
    "scope": "fleet" | "repo:<name>" | "lane:<name>",
    "confidence": <0.0-1.0>,
    "state": null | {{"subject": "<kebab>", "predicate": "<snake>", "value": "<short>"}}}}
Set "state" only for a fact that belongs in the fleet's key-value state store
(a stable attribute of a node/service/repo), else null."""

# instinct 3 backstop: drop any extracted fact that pattern-matches a credential,
# whatever the model was told.
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9]{8,}|AKIA[A-Z0-9]{12,}|ghp_[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]{20,}"
    r"|Bearer\s+\S{8,}|[A-Za-z0-9+/]{40,}={0,2}|\b[0-9a-f]{40,}\b"
    r"|api[_-]?key\s*[=:]|token\s*[=:]\s*\S|password\s*[=:]\s*\S)",
    re.IGNORECASE)

_SCOPE_RE = re.compile(r"^(fleet|repo:[\w./-]{1,64}|lane:[\w-]{1,64})$")
_SUBJECT_RE = re.compile(r"^[\w-]{1,64}$")
_PREDICATE_RE = re.compile(r"^[\w-]{1,64}$")


def load_key():
    k = os.environ.get("LITELLM_MASTER_KEY")
    if k:
        return k
    for f in ("/home/lumen/agent/.env", "/home/lumen/minerva/.env"):
        try:
            for ln in open(f):
                if ln.startswith("LITELLM_MASTER_KEY="):
                    return ln.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
    raise RuntimeError("no LITELLM_MASTER_KEY")


def chat(model, messages, max_tokens=1200):
    req = urllib.request.Request(
        GW + "/chat/completions",
        data=json.dumps({"model": model, "messages": messages,
                         "max_tokens": max_tokens, "temperature": 0.2}).encode(),
        headers={"Authorization": f"Bearer {load_key()}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def episode_transcript(events):
    """Compact, chronological text rendering of an episode for the extractor."""
    return "\n".join(
        json.dumps(ev, default=str)[:6000] for ev in events
    )[:MAX_TRANSCRIPT_CHARS]


def parse_facts(text, max_facts=MAX_FACTS):
    """Model output -> validated fact list. Tolerates a fenced/embedded array;
    everything that fails validation is dropped, never repaired."""
    if not isinstance(text, str):
        return []
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        raw = json.loads(m.group(0))
    except ValueError:
        return []
    if not isinstance(raw, list):
        return []
    facts = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        fact = item.get("fact")
        scope = item.get("scope", "fleet")
        if not isinstance(fact, str) or not fact.strip():
            continue
        fact = " ".join(fact.split())[:MAX_FACT_CHARS]
        if _SECRET_RE.search(fact):
            continue
        if not isinstance(scope, str) or not _SCOPE_RE.match(scope):
            scope = "fleet"
        try:
            confidence = min(1.0, max(0.0, float(item.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5
        state = item.get("state")
        if isinstance(state, dict):
            subj, pred = state.get("subject"), state.get("predicate")
            val = state.get("value")
            if not (isinstance(subj, str) and _SUBJECT_RE.match(subj)
                    and isinstance(pred, str) and _PREDICATE_RE.match(pred)
                    and isinstance(val, str) and val.strip()
                    and not _SECRET_RE.search(val)):
                state = None
            else:
                state = {"subject": subj, "predicate": pred,
                         "value": " ".join(val.split())[:200]}
        else:
            state = None
        facts.append({"fact": fact, "scope": scope,
                      "confidence": confidence, "state": state})
        if len(facts) >= max_facts:
            break
    return facts


def render_note(ticket_id, facts, meta):
    """Distilled markdown note. The `- [scope] fact` bullet shape is the recall
    contract — recall.py parses exactly this."""
    lines = [
        "---",
        f"source_ticket: wq#{ticket_id}",
        f"extracted_at: {meta.get('extracted_at', '')}",
        f"model: {meta.get('model', '')}",
        f"attempts: {meta.get('attempts', '')}",
        "source: distilled-episode",
        "---",
        "",
        f"# Distilled facts — wq#{ticket_id}",
        "",
        "## Facts",
    ]
    for f in facts:
        lines.append(f"- [{f['scope']}] {f['fact']} "
                     f"(confidence {f['confidence']:.2f})")
    state_facts = [f for f in facts if f.get("state")]
    if state_facts:
        lines += ["", "## Proposed lumen_state ingests"]
        for f in state_facts:
            s = f["state"]
            lines.append(f"- `{s['subject']}.{s['predicate']} = {s['value']}`")
    return "\n".join(lines) + "\n"


def write_note(ticket_id, facts, meta, store=None):
    store = store or DEFAULT_STORE
    facts_dir = os.path.join(store, "facts")
    os.makedirs(facts_dir, exist_ok=True)
    date = meta.get("extracted_at", "")[:10] or "undated"
    path = os.path.join(facts_dir, f"{date}-wq{ticket_id}.md")
    with open(path, "w") as f:
        f.write(render_note(ticket_id, facts, meta))
    git_commit(store, f"distill: wq#{ticket_id} ({len(facts)} facts)")
    return path


def git_commit(store, msg):
    """Best-effort: commit only if the store is already a git repo. Making it
    one is an ops decision (git init once), not this CLI's."""
    if not os.path.isdir(os.path.join(store, ".git")):
        return False
    try:
        subprocess.run(["git", "-C", store, "add", "-A"],
                       capture_output=True, timeout=30)
        p = subprocess.run(
            ["git", "-C", store, "-c", "user.email=lumen@fleet",
             "-c", "user.name=lumen", "commit", "-qm", msg],
            capture_output=True, timeout=30)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ingest_argvs(facts):
    """argv lists for the facts that carry a lumen_state triple. Printed as
    proposals by default; executed only under --ingest."""
    out = []
    for f in facts:
        s = f.get("state")
        if not s:
            continue
        out.append(["python3", LUMEN_STATE_CLI, "ingest", s["subject"],
                    s["predicate"], s["value"], "--source",
                    "foreman-distill"])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ticket", type=int, required=True)
    ap.add_argument("--runs-root", default=None,
                    help="override FOREMAN_RUNS_ROOT (episode source)")
    ap.add_argument("--store", default=None,
                    help="override FLEET_MEMORY_ROOT (distilled store)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-facts", type=int, default=MAX_FACTS)
    ap.add_argument("--ingest", action="store_true",
                    help="actually run the proposed lumen_state ingests "
                         "(default: print them as proposals)")
    a = ap.parse_args(argv)

    events = episodic.read_events(a.ticket, runs_root=a.runs_root)
    if not events:
        print(f"no episode for wq#{a.ticket} "
              f"(looked in {episodic.episode_path(a.ticket, a.runs_root)})")
        return 1

    raw = chat(a.model, [
        {"role": "system",
         "content": EXTRACT_SYSTEM.format(max_facts=a.max_facts)},
        {"role": "user", "content": episode_transcript(events)}])
    facts = parse_facts(raw, max_facts=a.max_facts)

    meta = {
        "extracted_at": datetime.datetime.now(datetime.timezone.utc)
                        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": a.model,
        "attempts": len([e for e in events if e.get("type") == "attempt_end"]),
    }
    if not facts:
        print(f"wq#{a.ticket}: 0 durable facts extracted — nothing written")
        return 0
    path = write_note(a.ticket, facts, meta, store=a.store)
    print(path)
    for argv_ in ingest_argvs(facts):
        if a.ingest:
            p = subprocess.run(argv_, capture_output=True, text=True,
                               timeout=60)
            print(f"ingested ({p.returncode}): {' '.join(argv_[2:])}")
        else:
            print(f"proposed: {' '.join(argv_)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
