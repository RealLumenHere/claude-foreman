#!/usr/bin/env python3
"""trigger — research pipeline v2 demand-driven trigger service (wq#3761,
design/wq3755_research_pipeline_v2.md §2+§5).

The only three ways research starts (§2):
  1. wq needs-tag.  An open ticket carries a `research(<question>)` tag — same
     tag(arg) shape as the repo()/dir() family (bin/wq:202 `_REPO_TAG_RE`,
     harness/needs.py's balanced-paren tag scanner), read from both the ticket's `needs` column and its
     detail's `Needs:` line (harness/needs.py — reused rather than re-regexed, since
     that module already unions both sources and "accepts anything tag-shaped").
     "unserved" has no new column/status (§2 explicitly refuses a schema migration):
     a tag is served once its brief file exists on disk.
  2. aq alerts escalated owner='research' (alerts table, source of truth wherever
     AQ_DB points — see tests/test_aq_escalate.py's ALERTS_SCHEMA for the shape).
  3. Manual: `trigger.py ask "<question>"` — also the replay path for testing.

For each unserved input this writes a narrow brief to briefs/<id>.md — the
rejection-ledger's last-50 constraint block (ledger.py, §5) prepended verbatim,
then the one question, its requesting context, and the target environment read
from fleet_hardware.json (never assumed) — before dispatching it to the LiteLLM
gateway at the (model, effort) the router's archetype table gives for
'research-synthesis' (orchestrator/router.py). The deliverable is a draft proposal
JSON matching the sandbox gate's schema (sandbox_gate.load_proposal); a draft with
no runnable smoke test is bounced once with a correction, then dropped (§2) — it
never reaches proposals/pending, so it never reaches the sandbox gate.

This service owns no model calls beyond dispatching the brief and never executes
proposal text itself: a dispatched draft is only ever written into
proposals/pending/, the sandbox gate's own inbox (sandbox_gate.INBOX), for the
lumen-research-sandbox.path unit to pick up (design §3's auditable-single-path
rule — see sandbox_gate.py's module docstring).

Usage:
  trigger.py scan [--db Q] [--aq-db A] [--briefs-dir D] [--pending-dir D] [--dry-run]
  trigger.py ask "<question>" [--context C] [--db Q] [--briefs-dir D] [--pending-dir D] [--dry-run]
"""
import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ledger  # noqa: E402
import sandbox_gate  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "harness"))
import needs  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "orchestrator"))
import router  # noqa: E402

WQ_DB = os.environ.get("WQ_DB", "/home/lumen/agents/work-queue/queue.db")
AQ_DB = os.environ.get("AQ_DB", "/home/lumen/agents/alert-queue/alerts.db")
BRIEFS_DIR = os.environ.get("RESEARCH_BRIEFS_DIR", os.path.join(HERE, "briefs"))
FLEET_HARDWARE = os.path.join(HERE, "fleet_hardware.json")
ARCHETYPE = "research-synthesis"
GATEWAY = os.environ.get("FOREMAN_LITELLM_BASE_URL", "http://localhost:4000")
KEY_FILE = os.environ.get("FOREMAN_LITELLM_KEY_FILE", "/home/lumen/.secrets/foreman-litellm.token")
TIMEOUT_S = 120
MAX_TOKENS = 2000

BOUNCE_NOTE = (
    "\n\nYour previous reply had no runnable smoke test. Resend the SAME proposal as "
    'one JSON object with a non-empty "smoke" field: a shell command that exits 0 '
    "only when the install actually worked. Reply with JSON only, no prose.")

SYSTEM_PROMPT = (
    "You are the research pipeline's drafting agent "
    "(design/wq3755_research_pipeline_v2.md §2). Read the brief and reply with "
    "EXACTLY ONE JSON object, no prose, no code fence, matching: "
    '{"id": "<slug matching [a-z0-9._-]+>", "title": "<one-line title>", '
    '"arch": "amd64" or "arm64", "install": "<shell: install steps, verbatim-runnable>", '
    '"smoke": "<shell: a test that exits 0 only if the install actually worked>"}. '
    "Every \"DO NOT re-propose\" line in the brief is a hard constraint: never draft a "
    "proposal that matches one. If you cannot produce a smoke test that genuinely exits "
    "non-zero on failure, omit \"smoke\" rather than inventing a command that always "
    "passes.")


def wq_conn(db=None):
    c = sqlite3.connect(db or WQ_DB, timeout=30)
    c.execute("PRAGMA query_only = 1")
    return c


def needs_column(c):
    return "needs" in {r[1] for r in c.execute("PRAGMA table_info(tasks)")}


def unserved_wq_questions(db=None, briefs_dir=BRIEFS_DIR):
    """[(brief_id, question, context)] for every open ticket's unserved
    research(...) tag — needs.parse() unions the `needs` column and the detail's
    `Needs:` line (harness/needs.py), so a tag authored either way is caught."""
    c = wq_conn(db)
    try:
        col = "needs" if needs_column(c) else "NULL"
        rows = c.execute(f"SELECT id, detail, {col} FROM tasks WHERE status='open'").fetchall()
    finally:
        c.close()
    out = []
    for tid, detail, needs_field in rows:
        for name, question in needs.parse(needs_field, detail).tags:
            if name != "research" or not question:
                continue
            brief_id = f"wq{tid}"
            if not os.path.exists(os.path.join(briefs_dir, f"{brief_id}.md")):
                out.append((brief_id, question, f"wq#{tid}"))
    return out


def unserved_aq_alerts(aq_db=None, briefs_dir=BRIEFS_DIR):
    """[(brief_id, question, context)] for every open alert escalated to research."""
    aq_db = aq_db or AQ_DB
    if not os.path.exists(aq_db):
        return []
    c = sqlite3.connect(aq_db, timeout=30)
    c.execute("PRAGMA query_only = 1")
    try:
        rows = c.execute(
            "SELECT id, title, message FROM alerts WHERE owner='research' AND status='open'"
        ).fetchall()
    finally:
        c.close()
    out = []
    for aid, title, message in rows:
        question = " ".join(f"{title or ''} {message or ''}".split())
        if not question:
            continue
        brief_id = f"aq{aid}"
        if not os.path.exists(os.path.join(briefs_dir, f"{brief_id}.md")):
            out.append((brief_id, question, f"aq#{aid}"))
    return out


_ARCH_ALIAS = {"x86_64": "amd64", "aarch64": "arm64"}


def target_env(node="lumen", fleet_hardware=FLEET_HARDWARE):
    """Target arch/os/node for the brief, read from fleet state (never assumed —
    §2). Defaults to 'lumen', the node sandbox_gate's container backend actually
    runs the gate on; a brief never guesses hardware the gate can't verify."""
    try:
        with open(fleet_hardware) as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return {"node": node, "arch": "amd64", "os": "unknown (fleet_hardware.json unreadable)"}
    n = manifest.get("nodes", {}).get(node, {})
    arch = n.get("arch")
    return {"node": node, "arch": _ARCH_ALIAS.get(arch, arch) or "amd64",
            "os": n.get("os") or "unknown"}


def constraint_block(db=None, n=ledger.CONSTRAINT_ROWS):
    c = ledger.connect(db or WQ_DB)
    try:
        return ledger.constraint_block(c, n)
    finally:
        c.close()


def brief_text(brief_id, question, context, env, constraints):
    """The brief (§2): constraint block prepended verbatim, then the one question,
    its requesting context, and the target environment."""
    parts = []
    if constraints:
        parts.append(constraints)
        parts.append("")
    parts += [
        f"# Research brief {brief_id}",
        "",
        f"Question: {question}",
        f"Requesting context: {context}",
        f"Target environment: {env['node']} ({env['arch']}, {env['os']})",
    ]
    return "\n".join(parts) + "\n"


def write_brief(brief_id, text, briefs_dir=BRIEFS_DIR):
    os.makedirs(briefs_dir, exist_ok=True)
    path = os.path.join(briefs_dir, f"{brief_id}.md")
    fd, tmp = tempfile.mkstemp(dir=briefs_dir, prefix=f".{brief_id}.")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)
    return path


def _api_key(key_file=KEY_FILE):
    key = os.environ.get("LITELLM_API_KEY", "").strip()
    if key:
        return key
    try:
        with open(key_file) as f:
            return f.read().strip() or None
    except OSError:
        return None


def gateway_chat(model, system, user, effort=None, gateway=GATEWAY, key_file=KEY_FILE,
                  timeout=TIMEOUT_S):
    """One completion against the LiteLLM gateway; None on any failure. A dead
    gateway must never break the trigger — everything downstream of here is pull,
    not critical path (§1)."""
    key = _api_key(key_file)
    if not key:
        return None
    body = {"model": model, "max_tokens": MAX_TOKENS,
            "messages": [{"role": "system", "content": system},
                        {"role": "user", "content": user}]}
    if effort:
        body["reasoning_effort"] = effort
    req = urllib.request.Request(
        f"{gateway}/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError, KeyError, IndexError, ValueError):
        return None


def extract_json(text):
    """The first JSON object in a model reply (handles ``` fences)."""
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                except ValueError:
                    return None
                return obj if isinstance(obj, dict) else None
    return None


def has_runnable_smoke(prop):
    return (isinstance(prop, dict)
            and isinstance(prop.get("smoke"), str) and prop["smoke"].strip()
            and isinstance(prop.get("install"), str) and prop["install"].strip()
            and prop.get("arch") in sandbox_gate.IMAGES)


def draft_proposal(brief_id, brief, rung, chat=gateway_chat):
    """Dispatch the brief; if the draft has no runnable smoke test, bounce once
    with a correction, then drop (§2 — unverifiable proposals are noise by
    definition). Returns a proposal dict ready for the sandbox gate's inbox, or
    None."""
    prompt = brief
    for attempt in range(2):
        reply = chat(rung.model, SYSTEM_PROMPT, prompt, rung.effort)
        prop = extract_json(reply)
        if has_runnable_smoke(prop):
            prop["brief_id"] = brief_id
            return prop
        prompt = brief + BOUNCE_NOTE
    return None


def submit_to_sandbox(prop, pending_dir):
    """Drop the draft into the sandbox gate's inbox — the only place a trigger
    worker touches proposal text; sandbox_gate.py's own unit runs it (§3)."""
    pid = prop.get("id")
    if not isinstance(pid, str) or not sandbox_gate.ID_RE.match(pid):
        pid = re.sub(r"[^a-z0-9._-]", "-", str(prop.get("brief_id") or "draft").lower())
        pid = pid.strip("-") or "draft"
    prop["id"] = pid
    os.makedirs(pending_dir, exist_ok=True)
    path = os.path.join(pending_dir, f"{pid}.json")
    fd, tmp = tempfile.mkstemp(dir=pending_dir, prefix=f".{pid}.")
    with os.fdopen(fd, "w") as f:
        json.dump(prop, f, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return path


def trigger_one(brief_id, question, context, db=None, briefs_dir=BRIEFS_DIR,
                 pending_dir=None, dry_run=False, chat=gateway_chat, manifest=None):
    """Brief -> (dry-run stop here) -> route -> dispatch -> bounce-or-drop ->
    sandbox inbox. Returns a report dict; never raises on a dead gateway/router."""
    env = target_env()
    constraints = constraint_block(db)
    text = brief_text(brief_id, question, context, env, constraints)
    path = write_brief(brief_id, text, briefs_dir)
    out = {"brief_id": brief_id, "brief_path": path, "context": context}
    if dry_run:
        return {**out, "verdict": "would_dispatch"}
    try:
        m = manifest or router.load_manifest()
        rung, reasons = router.select(m, ARCHETYPE)
    except router.ManifestError as e:
        return {**out, "verdict": "refused", "reasons": [str(e)]}
    if rung is None or rung.model == "DECOMPOSE":
        return {**out, "verdict": "refused", "reasons": reasons}
    prop = draft_proposal(brief_id, text, rung, chat)
    if not prop:
        return {**out, "verdict": "dropped",
                "reasons": ["no runnable smoke test after one bounce"]}
    dest = submit_to_sandbox(prop, pending_dir or sandbox_gate.INBOX)
    return {**out, "verdict": "dispatched", "proposal_path": dest}


def scan(db=None, aq_db=None, briefs_dir=BRIEFS_DIR, pending_dir=None, dry_run=False,
          chat=gateway_chat):
    inputs = unserved_wq_questions(db, briefs_dir) + unserved_aq_alerts(aq_db, briefs_dir)
    return [trigger_one(bid, q, ctx, db, briefs_dir, pending_dir, dry_run, chat)
            for bid, q, ctx in inputs]


def ask(question, context="manual", db=None, briefs_dir=BRIEFS_DIR, pending_dir=None,
        dry_run=False, chat=gateway_chat):
    brief_id = "ask-" + datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    return trigger_one(brief_id, question, context, db, briefs_dir, pending_dir, dry_run, chat)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="poll wq needs-tags + aq research-owned alerts, dispatch each unserved one")
    a = sub.add_parser("ask", help="manual trigger — also the replay path for testing")
    a.add_argument("question")
    a.add_argument("--context", default="manual")
    for p in (s, a):
        p.add_argument("--db", default=None, help="queue.db (default $WQ_DB or the live one)")
        p.add_argument("--aq-db", default=None, help="alerts.db (default $AQ_DB or the live one)")
        p.add_argument("--briefs-dir", default=BRIEFS_DIR)
        p.add_argument("--pending-dir", default=None, help="sandbox gate inbox (default sandbox_gate.INBOX)")
        p.add_argument("--dry-run", action="store_true", help="write the brief, skip dispatch")
    args = ap.parse_args(argv)
    if args.cmd == "ask":
        out = ask(args.question, args.context, args.db, args.briefs_dir, args.pending_dir,
                  args.dry_run)
    else:
        out = scan(args.db, args.aq_db, args.briefs_dir, args.pending_dir, args.dry_run)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
