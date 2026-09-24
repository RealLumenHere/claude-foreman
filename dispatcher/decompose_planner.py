#!/usr/bin/env python3
"""decompose_planner.py — planner, critic and wq surface for decomposer.py (wq#2515).

decomposer.py is the pure core: every guardrail runs against injected callables. This
module supplies the real callables and STAYS DARK — no dispatcher imports it (that is
wq#2516, phase 2); until then it is only invoked by hand via main() or by tests.

Boundaries:
  gateway   ONE planning call and one critic call, both through the LiteLLM gateway's
            OpenAI-compatible route (prompt-forge/llm.py pattern). Never the Anthropic
            API directly. A dead gateway or missing key raises, which decompose()
            turns into a refusal that files nothing.
  contract  the plan reply is parsed with harness/output_contract.py contract lines,
            per sub-ticket block. Any block that misses the contract is PlanRerun —
            the WHOLE plan is rejected, never a partial file. Same for a critic reply
            without a `VERDICT: PASS|FAIL` line: raised, so the core fails closed.
  routing   model + effort come from the `decomposer:` routing row in
            research/models.yaml (planning work: opus-5 at medium or high, never
            xhigh — anything outside {medium, high} is clamped to the row's intent).
  wq        writes go through /home/lumen/bin/wq (`add --archetype`, `edit --deps`,
            `note`, `done`); reads (parent, children-by-marker) are read-only sqlite
            on queue.db, the same direct-read pattern gates.py uses.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.request

from pydantic import BaseModel, field_validator

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "harness"))
import output_contract as oc  # noqa: E402

from decomposer import MARK_FROM, MAX_SUBS, MIN_SUBS, Sub, decompose  # noqa: E402

GATEWAY = os.environ.get("DECOMPOSER_LITELLM_URL", "http://127.0.0.1:4000")
KEY_FILE = os.environ.get(
    "DECOMPOSER_KEY_FILE",
    "/home/lumen/.secrets/litellm-service-keys/decomposer.key")
MODELS_YAML = os.environ.get(
    "DECOMPOSER_MODELS_YAML", os.path.join(REPO_ROOT, "research", "models.yaml"))
WQ_BIN = os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")
WQ_DB = os.environ.get("WQ_DB", "/home/lumen/agents/work-queue/queue.db")
TIMEOUT_S = 180
MAX_PLAN_TOKENS = 4000
FALLBACK_RUNG = ("claude-opus-5", "medium")


class PlanRerun(ValueError):
    """The reply missed the output contract. Raised, never partially parsed —
    decompose() catches it as a planner/critic failure and files nothing."""


# ---------------------------------------------------------------------------
# routing row
# ---------------------------------------------------------------------------

def routing_rung(path=MODELS_YAML):
    """(model_id, effort) from models.yaml routing.decomposer[0]; effort clamped to
    medium/high — the decomposer never runs xhigh (wq#2515 routing row)."""
    import yaml
    try:
        with open(path) as f:
            doc = yaml.safe_load(f) or {}
        spec = ((doc.get("routing") or {}).get("decomposer") or [None])[0]
    except OSError:
        spec = None
    if not spec:
        return FALLBACK_RUNG
    name, _, effort = str(spec).partition("@")
    effort = effort.split("(")[0].strip()
    if effort not in ("medium", "high"):
        effort = "high" if effort == "xhigh" else "medium"
    if not name.startswith("claude-"):
        name = "claude-" + name
    return name, effort


# ---------------------------------------------------------------------------
# gateway transport
# ---------------------------------------------------------------------------

def _api_key():
    key = os.environ.get("LITELLM_API_KEY", "").strip()
    if key:
        return key
    try:
        with open(KEY_FILE) as f:
            key = f.read().strip()
    except OSError:
        key = ""
    if not key:
        raise OSError(f"no LiteLLM key (env LITELLM_API_KEY or {KEY_FILE})")
    return key


def gateway_chat(system, user, *, max_tokens=MAX_PLAN_TOKENS):
    """One completion through the gateway; raises on any failure (fail-closed)."""
    model, effort = routing_rung()
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "reasoning_effort": effort,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        GATEWAY.rstrip("/") + "/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {_api_key()}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        doc = json.loads(resp.read())
    try:
        text = doc["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise OSError(f"gateway reply missing choices/message: {e}") from e
    if not text:
        raise OSError("gateway returned an empty completion")
    return text


# ---------------------------------------------------------------------------
# plan contract
# ---------------------------------------------------------------------------

class SubRoute(BaseModel):
    """Per-block contract lines. title/detail/deps are free-form and parsed below;
    these are the token fields output_contract can hold to."""
    subticket: int
    lane: str
    archetype: str


class CriticVerdict(BaseModel):
    verdict: str

    @field_validator("verdict", mode="before")
    @classmethod
    def _known(cls, v):
        v = str(v).strip().upper()
        if v not in ("PASS", "FAIL"):
            raise ValueError("verdict must be PASS or FAIL")
        return v


_DECOR = r"[\s>#*_\-]*"
_BLOCK_RE = re.compile(rf"(?im)^{_DECOR}SUBTICKET\s*:")
_TITLE_RE = re.compile(rf"(?im)^{_DECOR}TITLE\s*:\s*(.+?)\s*$")
_DEPS_RE = re.compile(rf"(?im)^{_DECOR}DEPS\s*:\s*([0-9,\s]+|none)\s*$")
_DETAIL_RE = re.compile(rf"(?im)^{_DECOR}DETAIL\s*:\s*$|^{_DECOR}DETAIL\s*:\s*(?=\S)")


def _blocks(text):
    starts = [m.start() for m in _BLOCK_RE.finditer(text or "")]
    return [text[s:e] for s, e in zip(starts, starts[1:] + [len(text)])]


def parse_plan(text):
    """LLM plan text -> [Sub]. Any block missing the contract raises PlanRerun; the
    plan is all-or-nothing so a malformed reply can never file a partial set."""
    blocks = _blocks(text)
    if not blocks:
        raise PlanRerun("no SUBTICKET blocks in the reply")
    parsed, num_to_idx = [], {}
    for i, block in enumerate(blocks):
        m = _DETAIL_RE.search(block)
        if not m:
            raise PlanRerun(f"sub[{i}] has no DETAIL: section")
        header, detail = block[:m.start()], block[m.end():].strip()
        res = oc.parse_or_reask(header, SubRoute)
        if res.status != "ok":
            raise PlanRerun(f"sub[{i}] contract {res.status}: {res.error[:200]}")
        t = _TITLE_RE.search(header)
        if not t:
            raise PlanRerun(f"sub[{i}] has no TITLE line")
        d = _DEPS_RE.search(header)
        if not d:
            raise PlanRerun(f"sub[{i}] has no DEPS line ('none' or numbers)")
        deps_raw = d.group(1).strip().lower()
        deps = () if deps_raw == "none" else tuple(
            int(n) for n in deps_raw.replace(" ", "").split(",") if n)
        num_to_idx[res.value.subticket] = i
        parsed.append((t.group(1), detail, res.value, deps))
    subs = []
    for i, (title, detail, route, deps) in enumerate(parsed):
        try:
            idx_deps = tuple(num_to_idx[n] for n in deps)
        except KeyError as e:
            raise PlanRerun(f"sub[{i}] DEPS references unknown subticket {e}") from e
        subs.append(Sub(title=title, detail=detail, lane=route.lane,
                        archetype=route.archetype, deps=idx_deps))
    return subs


# ---------------------------------------------------------------------------
# prompts + callables for decompose()
# ---------------------------------------------------------------------------

PLAN_SYSTEM = """You split one oversized fleet ticket into concrete sub-tickets.
Reply with one block per sub-ticket and NOTHING else, exactly this shape:

SUBTICKET: 1
TITLE: <one line, under 120 characters>
LANE: <a lane from the allowed list>
ARCHETYPE: <an archetype from the allowed list>
DEPS: none
DETAIL:
<the full working brief for one worker: scope, files/paths, acceptance criteria>

DEPS is either `none` or comma-separated numbers of EARLIER subtickets (e.g. 1,2).
Rules: {min}-{max} sub-tickets; each finishable by one worker in one session; details
concrete and self-contained (a worker sees only its own block); no two blocks overlap;
together the blocks cover the parent's whole scope."""

CRITIC_SYSTEM = """You are the fail-closed critic gate for a ticket decomposition.
Reject the plan unless: every sub-ticket is concrete and dispatchable on its own,
no two sub-tickets overlap, and together they cover the parent ticket's whole scope.
End your reply with exactly one line `VERDICT: PASS` or `VERDICT: FAIL`, followed by
a one-line reason."""


def _parent_text(parent):
    return (f"PARENT wq#{parent.get('id')}\nlane: {parent.get('lane')}\n"
            f"title: {parent.get('title')}\n\n{parent.get('detail') or ''}")


def make_plan_fn(valid_archetypes, lanes, llm=None):
    """plan_fn(parent) for decompose(). ONE planning call; a malformed reply raises
    PlanRerun which the core treats as a refusal (files nothing)."""
    call = llm or gateway_chat
    system = PLAN_SYSTEM.format(min=MIN_SUBS, max=MAX_SUBS)

    def plan_fn(parent):
        user = (_parent_text(parent)
                + "\n\nAllowed archetypes: " + ", ".join(sorted(valid_archetypes))
                + "\nAllowed lanes: " + ", ".join(sorted(lanes)))
        return parse_plan(call(system, user))
    return plan_fn


def make_critic_fn(llm=None):
    """critic_fn(parent, subs) -> (ok, reason). A reply without a VERDICT line
    raises PlanRerun: fail closed, never a pass by omission."""
    call = llm or gateway_chat

    def critic_fn(parent, subs):
        listing = "\n\n".join(
            f"SUB {i + 1} [{s.lane or parent.get('lane')}/{s.archetype}] {s.title}\n{s.detail}"
            for i, s in enumerate(subs))
        text = call(CRITIC_SYSTEM, _parent_text(parent) + "\n\nPLANNED SUBS:\n\n" + listing)
        res = oc.parse_or_reask(text, CriticVerdict)
        if res.status != "ok":
            raise PlanRerun(f"critic reply missed the contract: {res.error[:200]}")
        return res.value.verdict == "PASS", res.tail.strip()
    return critic_fn


# ---------------------------------------------------------------------------
# wq surface
# ---------------------------------------------------------------------------

_ADDED_RE = re.compile(r"added task (\d+)")


class WqCli:
    """The `wq` protocol decompose() needs, over the real queue: reads are read-only
    sqlite (gates.py pattern), writes go through the wq CLI so every add/edit/note
    keeps its dedup, gating and audit behaviour."""

    def __init__(self, wq_bin=WQ_BIN, db=WQ_DB):
        self.wq_bin, self.db = wq_bin, db

    def _ro(self):
        return sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)

    def _run(self, *args):
        p = subprocess.run([self.wq_bin, *args], capture_output=True, text=True,
                           timeout=60)
        if p.returncode != 0:
            raise RuntimeError(f"wq {args[0]} failed: {(p.stderr or p.stdout)[:200]}")
        return p.stdout

    def show(self, tid):
        with self._ro() as c:
            row = c.execute(
                "SELECT id, lane, title, detail, status, archetype FROM tasks "
                "WHERE id = ?", (int(tid),)).fetchone()
        if row is None:
            raise KeyError(f"wq#{tid} not found")
        return dict(zip(("id", "lane", "title", "detail", "status", "archetype"), row))

    def children(self, pid):
        # the trailing newline in the marker line keeps wq#900 from matching wq#9001
        with self._ro() as c:
            rows = c.execute(
                "SELECT id, status FROM tasks WHERE detail LIKE ? ORDER BY id",
                (f"%{MARK_FROM}{int(pid)}\n%",)).fetchall()
        return [{"id": r[0], "status": r[1]} for r in rows]

    def add(self, title, detail, lane, archetype, deps):
        args = ["add", "--title", title, "--detail", detail, "--lane", lane,
                "--archetype", archetype]
        if deps:
            args += ["--deps", ",".join(str(d) for d in deps)]
        out = self._run(*args)
        m = _ADDED_RE.search(out)
        if not m:
            raise RuntimeError(f"wq add gave no ticket id: {out[:200]}")
        return int(m.group(1))

    def set_deps(self, pid, ids):
        self._run("edit", str(int(pid)), "--deps", ",".join(str(i) for i in ids))

    def note(self, tid, text):
        self._run("note", str(int(tid)), text)

    def done(self, tid, notes):
        # wq#2517: the only done() caller is decompose()'s parent auto-close, and a
        # decomposed parent's evidence lives on its children — without --no-artifact
        # wq#759's gate refuses the close (the notes name tickets, not a file/PR/sha).
        self._run("done", str(int(tid)), "--notes", notes, "--no-artifact",
                  "decomposed parent; the evidence lives on the done children")


# ---------------------------------------------------------------------------
# manual entry (phase 2 wires the dispatcher to the same runtime())
# ---------------------------------------------------------------------------

def runtime():
    """(wq, plan_fn, critic_fn, valid_archetypes, routes_to_decompose, lanes) built
    from the live manifest — the exact kwargs decompose() takes."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "orchestrator"))
    import router
    man = router.load_manifest()
    lanes = set(router.LANE_REPO)
    routes = {a: man.chain(a) for a in router.ARCHETYPES}
    valid = {a for a, ch in routes.items() if ch and ch[0].model != "DECOMPOSE"}

    def routes_to_decompose(arch):
        ch = routes.get(arch)
        return not ch or ch[0].model == "DECOMPOSE"

    return dict(wq=WqCli(), plan_fn=make_plan_fn(valid, lanes),
                critic_fn=make_critic_fn(), valid_archetypes=valid,
                routes_to_decompose=routes_to_decompose, lanes=lanes)


def main(argv=None):
    ap = argparse.ArgumentParser(description="decompose one DECOMPOSE-routed ticket")
    ap.add_argument("--ticket", type=int, required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="plan + critic only; file nothing")
    a = ap.parse_args(argv)
    rt = runtime()
    parent = rt["wq"].show(a.ticket)
    if a.dry_run:
        subs = rt["plan_fn"](parent)
        ok, why = rt["critic_fn"](parent, subs)
        print(json.dumps({"critic_ok": ok, "critic_why": why,
                          "subs": [s.title for s in subs]}, indent=2))
        return 0
    res = decompose(parent, **rt)
    print(json.dumps({"status": res.status, "children": res.children,
                      "reasons": res.reasons}, indent=2))
    return 0 if res.status in ("filed", "already", "closed") else 1


if __name__ == "__main__":
    sys.exit(main())
