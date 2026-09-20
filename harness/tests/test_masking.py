"""wq#2275 (token-engine T6): credentials in the child's stream-json must be masked
before anything derived from the stream can reach disk, and a hit must alarm
(ntfy + rotation ticket) because the token already reached the model's context.

The integration test drives fworker.run_child() with a real subprocess emitting
NDJSON frames — the same plumbing test_loop_break_integration.py rides — and
asserts the synthetic ghp_ value is absent from every returned event while the
alarm fired exactly once.
"""
import hashlib
import json
import os
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fworker
import masking

# Synthetic, never-live values shaped like the real thing.
GHP = "ghp_" + "A1b2C3d4E5f6G7h8J9k0A1b2C3d4E5f6G7h8"
GHPAT = "github_pat_" + "11ABCDE0Y" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0u1V2w3X4y5Z6"
SK = "sk-ant-" + "x" * 24
AKIA = "AKIA" + "ABCDEFGHIJKLMNOP"

# wq#2292: strings that LOOK like a prefix rule but are not credentials. Each one
# is a real shape that reached a stream and cost a pri-1 rotation ticket or a
# failed broker verb. Both in-repo copies of the house set must stay silent on
# every one of them.
FALSE_POSITIVES = [
    # the exact string that fired on wq#2282: `ghs_solver` inside a repo name,
    # from the ISSUE-LOG row documenting this same bug in remote_credential_sweep
    "https://github.com/ERGO-Code/highs_solver.git flagged UNSTRIPPABLE",
    "clone git@github.com:o/ghp_notes.git",          # right prefix, body too short
    "the risk-assessment-framework doc needs review",  # `sk-` inside `risk-`
    "ghp_MASKED and github_pat_MASKED",  # our OWN output must not re-arm the alarm
    # wq#2298: the line that fired THIS ticket — wq#2292's worker grepping its own
    # run dir for mask residue. Six legal body characters after `ghp_`, so the old
    # floors read the mask literal as a token; the ticket that produced this string
    # was itself a rotation ticket, which is how the loop kept paying for itself.
    r'grep -rn "ghp_MASKED\|gho_MASKED\|ghu_MASKED\|ghs_MASKED\|ghr_MASKED'
    r'\|github_pat_MASKED" /home/lumen/foreman/_runs/wq2282/',
    "cfut_MASKED",                    # cloudflare: `MASKED` is 6 legal body chars
    "PAT: github_pat_MASKED",         # kv-secret eats a masked github value
    "sk-ant-xxxxxxxxMASKED",          # sk- backtracks into its own masked body
]

# One synthetic secret per RULE in PATTERNS, for the fixed-point property below.
SECRETS = [
    ("github", f'v = "{GHP}"'),
    ("github", f'v = "{GHPAT}"'),
    ("cloudflare", 'v = "cfut_v1.0-abcdefghijklmnopqrstuvwxyz0123456789"'),
    ("sk-key", f'v = "{SK}"'),
    ("slack", 'v = "xoxb-1234567890-abcdefgh"'),
    ("aws", f'v = "{AKIA}"'),
    ("kv-secret", 'GITEA_TOKEN="hunter2-hunter2-hunter2"'),
]


def test_patterns_mask_and_label():
    text = f"remote https://x:{GHP}@github.com and {SK} and {AKIA}"
    masked, hits = masking.mask_text(text)
    assert GHP not in masked and SK not in masked and AKIA not in masked
    assert "ghp_MASKED" in masked
    assert set(hits) == {"github", "sk-key", "aws"}


def test_github_pat_still_masked():
    # not a `TOKEN=` context: kv-secret would otherwise eat the prefix too
    masked, hits = masking.mask_text(f"remote https://x:{GHPAT}@github.com")
    assert GHPAT not in masked and "github_pat_MASKED" in masked
    assert hits == ["github"]   # one label even though two github rules exist


def test_false_positives_never_alarm():
    """wq#2292: the pri-1 rotation alarm must not fire on a repo name."""
    for text in FALSE_POSITIVES:
        masked, hits = masking.mask_text(text)
        assert hits == [], f"{text!r} falsely matched {hits}"
        assert masked == text, f"{text!r} was rewritten to {masked!r}"


def test_masking_never_re_arms_on_its_own_output():
    """wq#2298: mask(mask(x)) must add nothing — masking is a fixed point after one
    pass, for EVERY rule. A hit is a pri-1 rotate-today ticket whose worker reads
    the masked artefacts, so a rule that matches `<prefix>MASKED` files a fresh
    rotation ticket on every later read: wq#2282 -> #2292 -> #2298 were three of
    them, all for values that were the literal `MASKED`. The label coverage assert
    is the point of the test — a new pattern must prove it too."""
    assert {label for label, _ in SECRETS} == {label for label, _, _ in masking.PATTERNS}
    for label, text in SECRETS:
        once, first = masking.mask_text(text)
        assert first == [label], f"{label}: sample no longer fires ({first})"
        twice, again = masking.mask_text(once)
        assert again == [], f"{label}: masked output re-arms the alarm as {again}"
        assert twice == once, f"{label}: second pass rewrote {once!r} to {twice!r}"
        assert masking.scan(once)[1] == [], f"{label}: scan records its own output"


def test_a_real_secret_next_to_a_masked_one_still_fires():
    """The echo filter keys off the END of a match, so it must not swallow a live
    token that merely shares a line with an already-masked one."""
    masked, hits = masking.mask_text(f'prior: ghp_MASKED, current: {GHP}')
    assert hits == ["github"] and GHP not in masked
    assert masked == "prior: ghp_MASKED, current: ghp_MASKED"


def test_token_engine_mirror_agrees():
    """The daemon keeps its own copy of the house set (it runs as `tokeneng`, off
    this import path). Drift between them is silent, so pin them to each other:
    same verdict on every false positive AND on every real token."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                    "token-engine"))
    import token_engine_daemon as ted

    for text in FALSE_POSITIVES:
        assert ted.mask_hits(text) == [], f"{text!r} trips the daemon tripwire"
    for real in (GHP, GHPAT, SK, AKIA):
        assert ted.mask_hits(f'v = "{real}"'), f"daemon stopped catching {real[:8]}"
        assert masking.mask_text(f'v = "{real}"')[1], f"masking stopped catching {real[:8]}"
    # wq#2298: and the daemon must be silent on what masking.py WRITES, or quoting a
    # masked artefact in a PR body fails the verb (line 182) instead of paging.
    for label, text in SECRETS:
        once = masking.mask_text(text)[0]
        assert ted.mask_hits(once) == [], f"{label}: daemon trips on masked output"


def test_clean_text_untouched():
    text = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "cache_read_input_tokens: 1234"}]}})
    masked, hits = masking.mask_text(text)
    assert masked == text and hits == []


def test_masked_ndjson_line_still_parses():
    frame = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t0",
         "content": [{"type": "text", "text": f"origin https://x:{GHP}@github.com"}]}]}}
    masked, hits = masking.mask_text(json.dumps(frame))
    reparsed = json.loads(masked)   # masking must never corrupt the JSON
    assert hits == ["github"]
    assert GHP not in json.dumps(reparsed)


# --- wq#2288: the record that lets a rotation ticket name the credential ------------

PW = "hunter2-hunter2-hunter2"


def test_a_called_identifier_is_not_a_credential():
    # the exact line that fired the tripwire on wq#2284 and opened wq#2288
    masked, hits = masking.mask_text("        key = apply_key_allowlist(reg)")
    assert hits == [] and masked == "        key = apply_key_allowlist(reg)"
    masked, hits = masking.mask_text(
        'GH_TOKEN = os.environ.get("FOREMAN_GH_TOKEN", "/home/lumen/.secrets/gh.token")')
    assert hits == []


def test_real_kv_secrets_still_fire():
    for text in (f"password: {PW}", f'"api_key": "{PW}"', f"GITEA_TOKEN={PW}",
                 f"Signing Secret: {PW}", f"clientSecret: {PW}"):
        masked, hits = masking.mask_text(text)
        assert hits == ["kv-secret"], text
        assert PW not in masked, text


# wq#2296: the two tests above pin the false-positive shapes we already know, as
# string literals. They cannot catch the next one, and the next one arrives by a
# route peculiar to this rule: writing a false positive DOWN creates another
# instance of it. Doctrine makes every worker read kb/ISSUE-LOG.md before it
# changes anything, so that file is the one input reliably in some worker's context
# on every ticket — and it is where each of these gets documented, quoted verbatim.
# The unanchored `{6,}` rule therefore fired on the rows describing itself: rows
# 548/554/568 quote `highs_solver`, and the module's own `ghp_MASKED` output appears
# there too, which is how wq#2282 -> #2292 -> #2296 grew into 24 open pri-1
# "rotate today" tickets, each an xhigh worker rediscovering that `ghs_solver` is a
# repo name. Scanning the live corpus is the only gate that closes that loop: the
# next shape someone writes into the log fails here, not in the ticket queue.
DOCTRINE_CORPUS = "/home/lumen/kb/ISSUE-LOG.md"


def _describe(records):
    """Name the offenders without quoting one: scan() records carry the kept prefix,
    a length and 12 hex of sha256, and a context window cut from FULLY masked text."""
    return "corpus re-arms the alarm: " + "; ".join(
        "{label} {prefix}<{value_len} chars, sha {value_sha256_12}> in {context!r}"
        .format(**r) for r in records)


@pytest.mark.skipif(not os.path.exists(DOCTRINE_CORPUS),
                    reason="kb/ISSUE-LOG.md not on this host")
def test_doctrine_corpus_does_not_re_arm_the_alarm():
    with open(DOCTRINE_CORPUS, errors="replace") as fh:
        corpus = fh.read()
    _, records = masking.scan(corpus)
    assert records == [], _describe(records)


def test_masking_module_output_does_not_re_arm_on_its_own_source():
    """The module documents its own replacement text and its own prefix list. If
    either reads as a credential, every artefact it has already masked re-arms the
    alarm the next time a worker reads it."""
    with open(masking.__file__.replace(".pyc", ".py"), errors="replace") as fh:
        source = fh.read()
    _, records = masking.scan(source)
    assert records == [], _describe(records)


def test_scan_stays_in_lockstep_with_mask_text():
    text = f"remote https://x:{GHP}@github.com and {SK} and {AKIA} and password: {PW}"
    masked, hits = masking.mask_text(text)
    scanned, records = masking.scan(text)
    assert scanned == masked
    assert [r["label"] for r in records] == hits


def test_scan_records_identify_without_storing_the_value():
    scanned, records = masking.scan(f"$ litellm --config x.yaml\npassword: {PW}\n")
    assert [r["label"] for r in records] == ["kv-secret"]
    rec = records[0]
    assert rec["prefix"] == "password: "
    assert rec["value_len"] == len(PW)
    assert rec["value_sha256_12"] == hashlib.sha256(PW.encode()).hexdigest()[:12]
    # the record names the credential and its leak path, and carries neither value
    assert PW not in json.dumps(records) and PW not in scanned
    assert "litellm --config x.yaml" in rec["context"]
    assert "password: MASKED" in rec["context"]


def test_scan_context_is_cut_after_masking_not_before():
    # wq#1441's shape: a second credential inside the context window must arrive
    # already masked, never truncated into a usable fragment.
    _, records = masking.scan(f"password: {PW} " + "x" * 40 + f" remote https://x:{GHP}@h")
    context = records[0]["context"]
    assert GHP not in context and GHP[-20:] not in context
    assert "ghp_MASKED" in context


def test_scan_skips_a_value_an_earlier_pattern_already_masked():
    # `token: ghp_...` is one credential, not two: after the github pass the kv-secret
    # pattern sees `ghp_MASKED`, which is too short to match. The records must agree.
    scanned, records = masking.scan(f"token: {GHP}")
    assert [r["label"] for r in records] == ["github"]
    assert scanned == "token: ghp_MASKED"


def test_write_mask_hits_appends_and_holds_no_value(tmp_path, monkeypatch):
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path))
    _, records = masking.scan(f"password: {PW}")
    path = fworker.write_mask_hits(4242, 1, records)
    path = fworker.write_mask_hits(4242, 1, records)   # second alarm, same attempt
    with open(path) as f:
        written = json.load(f)
    assert len(written) == 2
    assert all(r["ts"] and r["label"] == "kv-secret" for r in written)
    assert PW not in json.dumps(written)


def make_leaky_child(tmp_path):
    """A child that emits a tool_result containing a synthetic ghp_ token, twice
    (alarm must still fire once), then a clean result frame."""
    script = textwrap.dedent(f"""\
        import json
        def emit(f):
            print(json.dumps(f), flush=True)
        emit({{"type": "system", "subtype": "init", "session_id": "mask-int",
              "model": "fake"}})
        for i in range(2):
            emit({{"type": "user", "message": {{"content": [
                {{"type": "tool_result", "tool_use_id": f"t{{i}}",
                  "content": [{{"type": "text",
                    "text": "remote: https://x:{GHP}@github.com/o/r.git"}}]}}]}}}})
        emit({{"type": "result", "subtype": "success", "is_error": False,
              "result": "done, remote was {GHP}"}})
    """)
    p = tmp_path / "leaky_child.py"
    p.write_text(script)
    return [sys.executable, str(p)]


def test_run_child_masks_stream_and_alarms_once(tmp_path, monkeypatch):
    alarms = []
    monkeypatch.setattr(fworker, "mask_alarm",
                        lambda tid, att, labels, records=None:
                        alarms.append((tid, att, labels, records)))
    state, exit_code, events, killed_reason, cpu_s, pid = fworker.run_child(
        make_leaky_child(tmp_path), str(tmp_path), dict(os.environ),
        max_turns=50, wall_clock_s=30, ticket_id=999998, attempt=1,
        frame_gap_s=10, post_result_grace_s=1)
    # the synthetic token never appears in anything fworker retained from the stream
    dump = json.dumps(events) + json.dumps(state.result_text or "")
    assert GHP not in dump
    assert "ghp_MASKED" in dump
    # alarm fired exactly once for the github label despite two leaky frames
    assert [(t, a, labels) for t, a, labels, _ in alarms] == [(999998, 1, ["github"])]
    # and it carried the record that names the token: prefix + digest, no value
    (record,) = alarms[0][3]
    assert record["label"] == "github" and record["prefix"] == "ghp_"
    assert record["value_sha256_12"] == hashlib.sha256(
        GHP[len("ghp_"):].encode()).hexdigest()[:12]
    assert "tool_result" in record["context"]
    assert GHP not in json.dumps(alarms)


def test_mask_alarm_pages_and_files_rotation_ticket(tmp_path, monkeypatch):
    pages, adds = [], []
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path))

    class P:
        returncode = 0
        stdout = "wq#424242 added"
        stderr = ""

    monkeypatch.setattr(fworker, "ntfy",
                        lambda title, msg, priority="default":
                        pages.append((title, priority)))
    monkeypatch.setattr(fworker, "wq", lambda *a, **k: adds.append(a) or P())
    _, records = masking.scan(f"remote https://x:{GHP}@github.com")
    fworker.mask_alarm(1234, 2, ["github"], records)
    assert pages and pages[0][0] == "foreman MASK HIT" and pages[0][1] == "high"
    assert adds and adds[0][0] == "add"
    assert any("rotation" in str(x) for x in adds[0])
    # the ticket must point at the records, or it arrives unidentifiable (wq#2288)
    hits_path = os.path.join(str(tmp_path), "wq1234", "mask-hits-2.json")
    assert any(hits_path in str(x) for x in adds[0])
    with open(hits_path) as f:
        assert json.load(f)[0]["prefix"] == "ghp_"
