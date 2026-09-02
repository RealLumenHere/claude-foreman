#!/usr/bin/env python3
"""wq#1193 probe — persona facts reach the Messenger prompt, and Messenger
replies reach the persona store.

Runs the REAL lumen_state.py against a throwaway sqlite file, so what it proves
is the production read path rather than a mock of it. Nothing here touches
minerva.db.

  python3 probe_persona_bridge.py [messenger-dir]

Deployed, it sits next to agent_bridge.py and needs no argument. Run from this
ops directory, name the checkout whose wiring it should check:

  python3 probe_persona_bridge.py /home/lumen/messenger
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MESSENGER_DIR = sys.argv[1] if len(sys.argv) > 1 else HERE

sys.path.insert(0, HERE)
import lumen_persona_bridge as pb  # noqa: E402

FAILED = []


def ok(name, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILED.append(name)


def state_on(db):
    """Point the loaded lumen_state module at a throwaway DB. Its DB path is a
    module constant, so overriding it after import is the only seam — the same
    one LPP's tools/lumen_state_json.py uses."""
    mod = pb._state()
    mod.DB = db
    mod.init_schema()
    # lumen_state.py defect, not probe scaffolding: fact_class/render_tier were
    # added to minerva.db by migration 0003 and are read by current() and written
    # by ingest(), but neither init_schema() nor _migrate() creates them. A DB
    # built purely from init_schema() therefore raises "no such column". Live
    # minerva.db has them, so this only bites a fresh file like this one.
    cols = {r[1] for r in mod._conn().execute("PRAGMA table_info(state_facts)")}
    with mod._conn() as c:
        for col in ("fact_class", "render_tier"):
            if col not in cols:
                c.execute(f"ALTER TABLE state_facts ADD COLUMN {col} TEXT")
    return mod


def main():
    db = os.path.join(tempfile.mkdtemp(prefix="wq1193-"), "probe.db")
    st = state_on(db)

    ok("recall is empty for an empty store", pb.recall_block() == "",
       repr(pb.recall_block()))
    ok("an empty store leaves the prompt untouched",
       pb.with_persona("SYSTEM") == "SYSTEM")

    # A fact the STREAMER stated. Predicate encoding is persona_store.ts's:
    # single-valued slots are the bare key, set members carry theirs after "__".
    st.ingest(pb.SUBJECT, "my_favourite_pokemon", "Bulbasaur",
              valid_from="2026-09-01", source_ref="lpp:stream")
    st.ingest(pb.SUBJECT, "i_like__caves", "caves",
              valid_from="2026-09-02", source_ref="lpp:stream")

    block = pb.recall_block()
    ok("a fact stated on stream is in the Messenger recall block",
       "- my favourite pokemon: Bulbasaur" in block, block)
    ok("a set member decodes to its key, not its raw predicate",
       "- i like: caves" in block, block)
    ok("identity slots rank ahead of set members",
       block.splitlines()[0].startswith("- my favourite pokemon"), block)

    prompt = pb.with_persona("SYSTEM")
    ok("the Messenger system prompt carries the persona block",
       pb.HEADER in prompt and "Bulbasaur" in prompt, prompt[-80:])
    ok("the block is appended, so worklog+persona stay a cacheable prefix",
       prompt.startswith("SYSTEM"))
    ok("recall honours its budget", len(pb.recall_block(20)) <= 20,
       str(len(pb.recall_block(20))))

    # A change of mind on the stream is a new open row; Messenger must show the
    # new value on the next turn, not the old one.
    st.ingest(pb.SUBJECT, "my_favourite_pokemon", "Charmander",
              valid_from="2026-09-02", source_ref="lpp:stream")
    block = pb.recall_block()
    ok("a change of mind supersedes in the Messenger prompt",
       "Charmander" in block and "Bulbasaur" not in block, block)

    # ── Reverse direction: what leaves Messenger for the store ───────────────
    ok("plain self-disclosure is ingestable",
       pb.is_safe_to_ingest("I used to drive a red Camaro back in Sacramento"))
    for unsafe in ("the gateway is at 100.121.17.5",
                   "my token is sk-abcd1234efgh",
                   "I restarted lumen-messenger for you",
                   "it lives in /home/lumen/agent",
                   "that landed in wq#1176"):
        ok(f"refused: {unsafe[:34]}", not pb.is_safe_to_ingest(unsafe))

    argv = pb.ingest_argv("My name is Lumen", "messenger")
    ok("ingest goes through LPP's cross-platform CLI, not a second extractor",
       argv[-4:] == ["state", "My name is Lumen", "--platform", "messenger"]
       and pb.PERSONA_CLI in argv, " ".join(argv))
    ok("ingest is tagged messenger, not stream", pb.PLATFORM == "messenger")

    # note_reply must never raise or block, whatever the state of LPP.
    pb.LPP_DIR = "/nonexistent-lpp"
    pb.note_reply("I am from Sacramento")
    pb.note_reply("my ip is 10.0.0.1")
    ok("note_reply survives a missing LPP checkout", True)

    # ── The wiring itself ────────────────────────────────────────────────────
    bridge = os.path.join(MESSENGER_DIR, "agent_bridge.py")
    if not os.path.exists(bridge):
        ok("agent_bridge.py is where the wiring check can see it", False,
           f"no {bridge} — pass the messenger checkout: "
           f"python3 {os.path.basename(__file__)} /home/lumen/messenger")
        print(f"\nFAILED: {', '.join(FAILED)}")
        return 1
    src = open(bridge).read()
    composed = re.findall(r"^\s*composed_system = .*$", src, re.M)
    ok("every messenger prompt composition injects the persona",
       len(composed) == 2 and all("_with_persona(" in c for c in composed),
       " | ".join(c.strip() for c in composed))
    ok("every persisted reply is mined for self-facts",
       src.count("_note_persona(reply)") == 3, str(src.count("_note_persona(reply)")))

    print(f"\n{'FAILED: ' + ', '.join(FAILED) if FAILED else 'all probes pass'}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
