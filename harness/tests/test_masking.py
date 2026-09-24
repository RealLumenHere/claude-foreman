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

# Synthetic, never-live values shaped like the real thing. Every one is SPLIT at its
# prefix so the source line does not match the rule it exercises: merely READING this
# file would otherwise trip the wq#2275 tripwire and file a pri-1 "rotate today" ticket
# per label. wq#2299 did this for test_token_injection.py, 74dcb82 for the cloudflare and
# slack constants here, and wq#2356 for the last three in test_mask_replay.py — whose
# source still fired at HEAD. test_the_mask_suite_does_not_arm_its_own_tripwire now holds
# the whole set to that rule, so the next inlined fixture fails a test instead of a lease.
# The concatenation leaves the runtime bytes identical, so the rules still get their
# real shape at assert time.
GHP = "ghp_" + "A1b2C3d4E5f6G7h8J9k0A1b2C3d4E5f6G7h8"
GHPAT = "github_pat_" + "11ABCDE0Y" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0u1V2w3X4y5Z6"
SK = "sk-ant-" + "x" * 24
AKIA = "AKIA" + "ABCDEFGHIJKLMNOP"
CFUT = "cfut_" + "v1.0-abcdefghijklmnopqrstuvwxyz0123456789"
XOXB = "xoxb-" + "1234567890-abcdefgh"
# The kv rule keys off the NAME, so the split has to fall between `TOKEN="` and the
# value — that is the boundary the rule reads, and `'` is not in its body charset.
KV_LINE = 'GITEA_TOKEN="' + 'hunter2-hunter2-hunter2"'
# wq#2297: needs no split — `PW` is not a credential label under any rule, and the
# kv fixtures below interpolate it, so each source line breaks at `{` before the
# body charset can start.
PW = "hunter2-hunter2-hunter2"

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
    # wq#2299's three sk-key hits, hash-matched back to their source by wq#2326:
    # hyphenated prose from a PR body, in the line that was explaining this very
    # bug. Every body is exactly 16 characters, because the greedy prefix gives
    # back only as many letters as the floor demands — so a false positive always
    # lands on the floor, where a length test cannot tell it from a short token.
    # The digit run earns the second one its place: bodies admit digits, prefixes
    # do not, so it is the only one of the three that exercises both charsets.
    "`task-engine-daemon-restart`",
    "`risk-assessment-matrix-2026`",
    "`disk-usage-report-generator`",
    "ghp_MASKED and github_pat_MASKED",  # our OWN output must not re-arm the alarm
    # wq#2298: the line that fired THIS ticket — wq#2292's worker grepping its own
    # run dir for mask residue. Six legal body characters after `ghp_`, so the old
    # floors read the mask literal as a token; the ticket that produced this string
    # was itself a rotation ticket, which is how the loop kept paying for itself.
    r'grep -rn "ghp_MASKED\|gho_MASKED\|ghu_MASKED\|ghs_MASKED\|ghr_MASKED'
    r'\|github_pat_MASKED" /home/lumen/foreman/_runs/wq2282/',
    # wq#2400: the prefix one separator inside an identifier, which `(?<![A-Za-z0-9])`
    # still read as a token. The first line is the fixture wq#2316 drafted for
    # ops/wq2316-mask-secrets-boundary and dropped because the rule masked it; the
    # snapshot of that draft then fired the slack alarm and filed a rotate-today ticket
    # whose value was two English words.
    "see notes_on_xoxb-plainword",
    "reading my_cfut_settings_override",
    "log_sk-report-generator-2026",
    "cfut_MASKED",                    # cloudflare: `MASKED` is 6 legal body chars
    "PAT: github_pat_MASKED",         # kv-secret eats a masked github value
    "sk-ant-xxxxxxxxMASKED",          # sk- backtracks into its own masked body
]

# One synthetic secret per RULE in PATTERNS, for the fixed-point property below.
SECRETS = [
    ("github", f'v = "{GHP}"'),
    ("github", f'v = "{GHPAT}"'),
    ("cloudflare", f'v = "{CFUT}"'),
    ("sk-key", f'v = "{SK}"'),
    ("slack", f'v = "{XOXB}"'),
    ("aws", f'v = "{AKIA}"'),
    ("kv-secret", KV_LINE),
]

# wq#2297: the same list for the kv-secret rule, whose label half was a naked keyword
# alternation. Every line here is ordinary source that the rule read as a credential,
# arming a pri-1 rotate-today alarm; the cascade cost four tickets (wq#2288 on wq#2284,
# wq#2292 on wq#2282, wq#2297 on wq#2290) and none of them found a credential.
KV_FALSE_POSITIVES = [
    f"monkey: {PW}",                                # `KEY` had no left boundary
    f"hockey = {PW}",
    "    top = max(set(verdicts), key=verdicts.count)",   # bare `key=` is a kwarg
    '    FAKE_PAT = "sentinel-value-x"',                  # bare `PAT` is a var name
    "        token = read_oauth_token()",                 # the wq#2290 shape
    "        key = apply_key_allowlist(reg)",             # the wq#2284 shape
    f"key: {PW}",                                   # bare `key:` is ordinary YAML
    "token: os.environ.FOREMAN_GH_TOKEN",           # a pointer, not the credential
    "public key: AAAAB3NzaC1yc2EAAAADAQ",           # a public key is not a secret
]

# and the shapes that must keep firing — a rule this narrow is only safe if it still
# catches. Real leaks label the value with a qualified noun, in any separator style.
KV_REAL = [f"password: {PW}", f'"api_key": "{PW}"', f"GITEA_TOKEN={PW}",
           f"Signing Secret: {PW}", f"clientSecret: {PW}", f"apiKey: {PW}",
           f"X-Api-Key: {PW}", f"signing key = {PW}"]


# wq#2356: a worker investigating a mask alarm necessarily READS this file set, so a
# fixture left whole in source arms the tripwire on the reader and files a pri-1
# rotate-today ticket against a value that was never live. That is the loop ISSUE-LOG
# rows 568-599 are made of — the wq#2275 cascade spent six tickets on it and found no
# credential. The guard is on the SOURCE text, not the runtime value: split each fixture
# at the boundary its rule reads (see KV_LINE) and the assertions are unaffected.
MASK_SUITE_SOURCES = [
    "tests/test_masking.py", "tests/test_mask_replay.py", "tests/test_token_injection.py",
    "masking.py", "mask_replay.py", "burn_filter.py",
    # wq#2400: the mask-secrets bundle belongs to the same corpus — its four whole
    # fixtures are what a run editing it (wq#2316) put in front of the model, and the
    # alarm that followed cost this ticket.
    "../ops/wq2316-mask-secrets-boundary/test_mask_secrets_boundary.py",
    "../ops/wq2316-mask-secrets-boundary/mask-secrets",
]


def test_the_mask_suite_does_not_arm_its_own_tripwire():
    harness = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    offenders = {}
    for rel in MASK_SUITE_SOURCES:
        with open(os.path.join(harness, rel), errors="replace") as fh:
            hits = masking.scan(fh.read())[1]
        # label/prefix/length only — naming the value here would burn it for real
        if hits:
            offenders[rel] = [(h["label"], h["prefix"], h["value_len"]) for h in hits]
    assert offenders == {}, (
        "these sources carry a whole credential shape, so merely reading them files a "
        "rotation ticket: %r — split each across a concatenation at the boundary its "
        "rule reads, which leaves the runtime bytes identical" % offenders)


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

    for text in FALSE_POSITIVES + KV_FALSE_POSITIVES:
        assert ted.mask_hits(text) == [], f"{text!r} trips the daemon tripwire"
    # wq#2297: the daemon's kv_secret must also still CATCH, or the mirror passes by
    # going blind — a false hit there fails the verb, a missed one leaks the result.
    for text in KV_REAL:
        assert ted.mask_hits(text), f"daemon stopped catching {text!r}"
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

# wq#2300: (label, the prefix the mask may keep, a sample that starts with it). The
# prefix is the non-secret part a rotation worker reads to know WHICH credential
# fired — `ghp_`, `AKIA`, a key name; everything after it is the credential. Samples
# are the constants above, so this table cannot drift from the fixtures the rest of
# the suite uses, and the kv one splits at the boundary its rule reads.
BODIES = [
    ("github", "ghp_", GHP),
    ("github", "github_pat_", GHPAT),
    ("cloudflare", "cfut_", CFUT),
    ("sk-key", "sk-ant-", SK),
    ("slack", "xoxb-", XOXB),
    ("aws", "AKIA", AKIA),
    ("kv-secret", 'TOKEN="', 'TOKEN="' + PW),
]


def test_the_mask_keeps_the_prefix_and_no_character_of_the_body():
    """wq#2300: a greedy prefix group prints part of the secret. `sk-[a-zA-Z-]*` ran
    as far as the `{16,}` floor would allow, so a body opening with a long letter run
    was partly kept: `sk-ant-` + 24 letters masked to the prefix plus EIGHT characters
    of the body, written into every artefact derived from the stream. It also broke
    identification — those 8 landed in scan()'s `prefix`, the field contracted to hold
    no value, and `value_sha256_12` was then taken over the 16-char remainder, so
    hashing the real token (what every rotation ticket instructs its worker to do)
    could not match.

    The invariant is stated for EVERY rule, with the same label-coverage assert as the
    fixed-point test above: what survives a mask is exactly the declared prefix.
    """
    assert {label for label, _, _ in BODIES} == {l for l, _, _ in masking.PATTERNS}
    for label, prefix, sample in BODIES:
        body = sample[len(prefix):]
        text = f"x {sample} y"
        masked, hits = masking.mask_text(text)
        assert hits == [label], f"{label}: sample no longer fires ({hits})"
        assert masked == f"x {prefix}MASKED y", f"{label}: mask kept part of the body"

        _, records = masking.scan(text)
        assert [r["label"] for r in records] == [label]
        assert records[0]["prefix"] == prefix, f"{label}: record widened the prefix"
        # the WHOLE body is hashed, so hashing a candidate can identify the credential
        assert records[0]["value_len"] == len(body)
        assert records[0]["value_sha256_12"] == hashlib.sha256(
            body.encode()).hexdigest()[:12], f"{label}: hash is over a truncated body"


def test_a_called_identifier_is_not_a_credential():
    # the exact line that fired the tripwire on wq#2284 and opened wq#2288
    masked, hits = masking.mask_text("        key = apply_key_allowlist(reg)")
    assert hits == [] and masked == "        key = apply_key_allowlist(reg)"
    masked, hits = masking.mask_text(
        'GH_TOKEN = os.environ.get("FOREMAN_GH_TOKEN", "/home/lumen/.secrets/gh.token")')
    assert hits == []


# (the positive half of this rule is KV_REAL / test_a_qualified_noun_still_fires)


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


# wq#2310: the corpus test above has a twin that kept being rediscovered one file at
# a time — wq#2296 pinned masking.py, wq#2303 pinned this file — while the file that
# actually fired wq#2310 went unpinned. That was harness/fworker.py: wq#2291 read it
# five times in one attempt, and its `token = read_oauth_token()` shapes matched the
# rule deployed that minute. The invariant is not per-file. Everything the alarm is
# built out of gets read BY the worker the alarm sends, so no file on that path may
# read as a credential — the ticket the tripwire files is itself a stream that the
# tripwire watches.
REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
ALARM_READ_PATH = [
    "harness/masking.py",        # the rules, and the comments quoting the shapes
    "harness/burn_filter.py",    # they must not match
    "harness/fworker.py",        # mask_alarm / screen_mask_hits / write_mask_hits
    "harness/live_code.py",      # wq#2404: how both of those reach a run in flight
    # and the ticket's own text, which every answering worker reads first
    "harness/prompts/rotation-ticket.md",
    "harness/tests/test_masking.py",
    "harness/tests/test_burn_filter.py",
    "harness/tests/test_token_injection.py",  # wq#2299: the oauth fixtures
    "token-engine/token_engine_daemon.py",    # the mirror of PATTERNS
    "token-engine/gate_replay.sh",            # and its tripwire probe
]


@pytest.mark.parametrize("rel", ALARM_READ_PATH)
def test_the_alarms_own_read_path_does_not_re_arm_it(rel):
    """Reading any of these must not page Frank at pri 1.

    wq#2299's chain guard only suppresses the CHILD ticket when the current ticket is
    already a rotation ticket; on an ordinary ticket a literal here still files one
    pri-1 rotate-today row per label. Assert the real file on disk rather than a
    fixture string — the read that fires is of the file.
    """
    with open(os.path.join(REPO, rel), errors="replace") as fh:
        source = fh.read()
    masked, records = masking.scan(source)
    assert records == [], f"{rel} " + _describe(records)
    assert masked == source


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


# --- wq#2297: the label half must not read ordinary source as a credential ----------

def test_ordinary_source_is_not_a_credential_label():
    for text in KV_FALSE_POSITIVES:
        masked, hits = masking.mask_text(text)
        assert hits == [], f"{text!r} falsely matched {hits}"
        assert masked == text, f"{text!r} was rewritten to {masked!r}"


def test_a_qualified_noun_still_fires():
    """The narrowing is only safe if the rule still catches: a real leak labels its
    value with a qualified noun, in whatever separator style the source service uses."""
    for text in KV_REAL:
        masked, hits = masking.mask_text(text)
        assert hits == ["kv-secret"], text
        assert PW not in masked, text


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
    # wq#2393: the context is the text the frame handed the model, not the frame — the
    # envelope's `tool_result` scaffolding is no longer part of what the record quotes
    assert record["context"] == "remote: https://x:ghp_MASKED@github.com/o/r.git"
    assert GHP not in json.dumps(alarms)


def test_a_retry_does_not_re_page_on_what_attempt_one_already_filed(
        tmp_path, monkeypatch):
    """wq#2404: the dedup sets were local to run_child(), so they reset on every
    attempt — and the PROCESS does not reset, it holds the whole ticket. wq#2343
    (attempt 1) and wq#2348 (attempt 2) are one run 110 seconds apart, sharing the
    `cloudflare` label, two pri-1 rotate-today rows for the same committed fixtures.
    Same stream, two attempts, one alarm."""
    alarms = []
    monkeypatch.setattr(fworker, "mask_alarm",
                        lambda tid, att, labels, records=None:
                        alarms.append((tid, att, labels)))
    child = make_leaky_child(tmp_path)
    for attempt in (1, 2):
        fworker.run_child(child, str(tmp_path), dict(os.environ), max_turns=50,
                          wall_clock_s=30, ticket_id=999997, attempt=attempt,
                          frame_gap_s=10, post_result_grace_s=1)
    assert alarms == [(999997, 1, ["github"])]
    # a different ticket in the same process keeps its own sets — the dedup is the
    # ticket's, not the fleet's.
    fworker.run_child(child, str(tmp_path), dict(os.environ), max_turns=50,
                      wall_clock_s=30, ticket_id=999996, attempt=1,
                      frame_gap_s=10, post_result_grace_s=1)
    assert alarms[-1] == (999996, 1, ["github"])


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
    # wq#2299 put a `wq show` (the chain guard's title lookup) ahead of the add, so
    # pick the add out of the calls rather than assuming it is the first one.
    (add,) = [c for c in adds if c[0] == "add"]
    assert any("rotation" in str(x) for x in add)
    # the ticket must point at the records, or it arrives unidentifiable (wq#2288)
    hits_path = os.path.join(str(tmp_path), "wq1234", "mask-hits-2.json")
    assert any(hits_path in str(x) for x in add)
    with open(hits_path) as f:
        assert json.load(f)[0]["prefix"] == "ghp_"


class WqResult:
    """What fworker.wq() hands back: the CLI's exit code and its two streams."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def duplicate_out(ticket, title):
    """bin/wq cmd_add's refusal, verbatim in shape (wq#2341). The id in it is the row
    this title is already open on, and it is the only place fworker learns which."""
    return (f"DUPLICATE — task {ticket} [fleet/open] already has this title:\n"
            f"  {title}\nUse --force to add anyway.")


def github_records():
    _, records = masking.scan(f"remote https://x:{GHP}@github.com")
    return records


def alarm(tmp_path, monkeypatch, ticket_id, records, labels=("github",),
          add_result=None, title="[token-engine T10] remaining verbs"):
    """mask_alarm on a ticket whose own title is `title`, with `wq add` answering
    `add_result`. Returns (pages, calls): every ntfy and every wq argv, in order."""
    pages, calls = [], []
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path))
    monkeypatch.setattr(fworker, "ntfy",
                        lambda t, msg, priority="default":
                        pages.append((t, priority, msg)))

    def fake_wq(*args, **kwargs):
        calls.append(args)
        return add_result if (args[0] == "add" and add_result) else WqResult()

    monkeypatch.setattr(fworker, "wq", fake_wq)
    monkeypatch.setattr(fworker, "wq_show", lambda tid: {"title": title})
    fworker.mask_alarm(ticket_id, 1, list(labels), records)
    return pages, calls


def alarm_with_title(tmp_path, monkeypatch, title, ticket_id):
    """Run mask_alarm against a ticket whose title is `title`; return (pages, calls)."""
    return alarm(tmp_path, monkeypatch, ticket_id, github_records(), title=title)


def test_a_rotation_ticket_refire_is_noted_not_filed(tmp_path, monkeypatch):
    """wq#2299: reading the artefacts a rotation ticket points at is the work, and on
    this stream that read is the next hit. One child ticket per read makes the tripwire
    self-sustaining — 34 rotation tickets, 22 still open, inside an hour."""
    pages, calls = alarm_with_title(
        tmp_path, monkeypatch,
        "[rotation] burned credential in wq#2290 transcript (sk-key)", 2299)
    assert [c[0] for c in calls] == ["note"]      # no `add`: no child ticket
    assert calls[0][1] == "2299"                  # noted on the ticket being worked
    # the evidence still lands, and the note says where
    hits_path = os.path.join(str(tmp_path), "wq2299", "mask-hits-1.json")
    with open(hits_path) as f:
        assert json.load(f)[0]["prefix"] == "ghp_"
    assert hits_path in calls[0][2]
    assert pages and pages[0][0] == "foreman MASK HIT (rotation chain)"


def test_the_verify_pass_of_a_rotation_ticket_is_on_the_chain(tmp_path, monkeypatch):
    """wq#2308: every rotation ticket spawns a verify row that carries the marker in
    the MIDDLE of its title, and sends its worker back to the same run dir — where 9
    of the frozen snapshots still come back `burned` (a fixture that has since been
    split, a `grant-dir-<hash>` runtime id: real text, in no tracked file today). A
    prefix-only guard gave each of those a fresh pri-1 child."""
    pages, calls = alarm_with_title(
        tmp_path, monkeypatch,
        "[verify] wq#2294 [rotation] burned credential in wq#2284 transcript (github)",
        2321)
    assert [c[0] for c in calls] == ["note"]      # no `add`: no child ticket
    assert calls[0][1] == "2321"                  # noted on the verify row itself
    assert pages and pages[0][0] == "foreman MASK HIT (rotation chain)"


def test_a_value_keyed_rotation_row_is_still_on_the_chain(tmp_path, monkeypatch):
    """wq#2341 dropped the run id out of the title, so the chain marker had to stop
    before it. Both generations are in the queue at once — 47 rows say `in wq#M
    transcript`, everything filed since names a hash — and working either one is the
    read that fires the next hit."""
    pages, calls = alarm_with_title(
        tmp_path, monkeypatch,
        "[rotation] burned credential sha256:20227a136ec3 (github)", 2341)
    assert [c[0] for c in calls] == ["note"]      # no `add`: no child ticket
    assert calls[0][1] == "2341"
    assert pages and pages[0][0] == "foreman MASK HIT (rotation chain)"


def test_an_ordinary_ticket_still_files_a_rotation_ticket(tmp_path, monkeypatch):
    # the guard is scoped to the chain: any other ticket alarms exactly as before.
    pages, calls = alarm_with_title(
        tmp_path, monkeypatch, "[token-engine T10] remaining verbs", 2278)
    assert [c[0] for c in calls] == ["add"]
    assert pages and pages[0][1] == "high"


def test_the_rotation_ticket_names_the_settle_tool(tmp_path, monkeypatch):
    """wq#2332: the ticket must send its worker to mask_replay.py, not only to the raw
    records. wq#2307 built the tool that settles these rows from the frozen corpus; the
    ticket text predates it and described the hand path alone, so wq#2332's worker
    rebuilt that corpus walk from scratch — and the hand-grep it prescribes is itself
    what re-arms the alarm (wq#2290 -> #2297 -> #2303). The run id must be in the
    command: the tool takes it as its only required argument."""
    _, calls = alarm_with_title(
        tmp_path, monkeypatch, "[token-engine T10] remaining verbs", 2278)
    (add,) = [c for c in calls if c[0] == "add"]
    detail = next(x for x in add if "wq#2275 mask tripwire" in str(x))
    assert f"{fworker.MASK_REPLAY} 2278" in detail
    assert os.path.exists(fworker.MASK_REPLAY)  # a pointer to nothing costs the hour


def test_a_template_fix_reaches_a_run_in_flight(tmp_path, monkeypatch):
    """wq#2404: the ticket TEXT was the third thing frozen at spawn. wq#2348 was filed
    23:01:44Z carrying the template 32aa000 replaced at 22:40:21Z, because the process
    writing it imported fworker at 22:14:43Z — and the text is the only part of the
    alarm the answering worker reads. Reading it per alarm is what makes an edit land
    on the runs already going."""
    tmpl = tmp_path / "rotation-ticket.md"
    tmpl.write_text("pattern(s) {{TAG}} on wq#{{TICKET_ID}} attempt {{ATTEMPT}}. "
                    "Run `python3 {{MASK_REPLAY}} {{TICKET_ID}}`; records in "
                    "{{HITS_PATH}}. A NEW INSTRUCTION, deployed mid-run.\n")
    monkeypatch.setattr(fworker, "ROTATION_DETAIL_PATH", str(tmpl))
    _, calls = alarm_with_title(
        tmp_path, monkeypatch, "[token-engine T10] remaining verbs", 2404)
    (add,) = [c for c in calls if c[0] == "add"]
    detail = next(x for x in add if "A NEW INSTRUCTION" in str(x))
    assert f"{fworker.MASK_REPLAY} 2404" in detail
    assert "pattern(s) github on wq#2404 attempt 1." in detail


@pytest.mark.parametrize("body", [
    None,                                        # the deploy removed the file
    "a template that forgot to name the replay tool or the records",
    "{{TAG}} on wq#{{TICKET_ID}}: python3 {{MASK_REPLAY}} {{TICKET_ID}}, {{HITS_PAH}}",
])
def test_a_broken_template_still_files_an_actionable_row(
        tmp_path, monkeypatch, body):
    """One-directional, like both its siblings: a missing, gutted or half-expanded
    template must not be able to quiet the alarm or strip the two pointers a rotation
    row cannot be settled without — wq#2288 arrived without them and was unactionable.
    It falls back to the text frozen in this process instead."""
    tmpl = tmp_path / "rotation-ticket.md"
    if body is not None:
        tmpl.write_text(body)
    monkeypatch.setattr(fworker, "ROTATION_DETAIL_PATH", str(tmpl))
    pages, calls = alarm_with_title(
        tmp_path, monkeypatch, "[token-engine T10] remaining verbs", 2404)
    (add,) = [c for c in calls if c[0] == "add"]
    detail = next(x for x in add if "wq#2275 mask tripwire" in str(x))
    assert f"{fworker.MASK_REPLAY} 2404" in detail
    assert os.path.join(str(tmp_path), "wq2404", "mask-hits-1.json") in detail
    assert pages and pages[0][1] == "high"


# --- wq#2341: one rotation row per burned VALUE, not per run that printed it --------

def test_the_rotation_title_is_the_value_not_the_run():
    """The defect: titled by the run, one value printing in ten runs opened ten pri-1
    rotate-today rows — 28 were open at once on 2026-09-20, each drawing an xhigh
    worker. Titled by the value, `wq add`'s own title check collapses them."""
    sha = github_records()[0]["value_sha256_12"]
    first = fworker.rotation_title(2284, "github", [sha])
    later = fworker.rotation_title(9999, "github", [sha])
    assert first == later
    assert sha in first
    assert "wq#" not in first          # nothing run-specific left to split the row on
    assert GHP not in first            # the title names the value, never carries it
    # and the chain guard still recognises its own row (wq#2299/wq#2308)
    assert fworker.ROTATION_TITLE_PREFIX in first


def test_a_different_value_is_a_different_row():
    """The dedupe may only ever collapse the SAME credential. A second value — or one
    more value alongside the first — has never been paged on, and must be."""
    a, b = "a" * 12, "b" * 12
    titles = {fworker.rotation_title(1, "github", [a]),
              fworker.rotation_title(1, "github", [b]),
              fworker.rotation_title(1, "github", [a, b])}
    assert len(titles) == 3
    # a set is keyed by order-independent identity: the same two values, either way up
    assert (fworker.rotation_title(1, "github", [a, b])
            == fworker.rotation_title(1, "github", [b, a]))
    assert "2 values" in fworker.rotation_title(1, "github", [a, b])


def test_only_a_burned_label_keys_the_row():
    """`records` is a re-scan of the whole line, so it also holds the values of labels
    burn_filter screened as committed text. Keying on one of those would let a fixture
    sharing a line with a real token decide which row the token dedupes into."""
    _, records = masking.scan(f"remote https://x:{GHP}@github.com and {KV_LINE}")
    assert {r["label"] for r in records} == {"github", "kv-secret"}
    burned = fworker.burned_hashes(["github"], records)
    assert burned == [r["value_sha256_12"] for r in records if r["label"] == "github"]


def test_an_alarm_with_no_record_keeps_the_run_keyed_title(tmp_path, monkeypatch):
    """write_mask_hits is best-effort by contract and scan() can come back empty. With
    no hash there is nothing to prove two alarms are the same value, so the row falls
    back to one per run — noisy, and still better than one row standing in for values
    that were never shown to be the same."""
    _, calls = alarm(tmp_path, monkeypatch, 2278, None)
    (add,) = [c for c in calls if c[0] == "add"]
    assert "[rotation] burned credential in wq#2278 transcript (github)" in add


def test_the_same_value_in_a_later_run_is_a_note_not_a_second_row(tmp_path,
                                                                  monkeypatch):
    """The fan-out this closes. The second run still files — the queue is what decides
    — and takes the DUPLICATE refusal as its answer: the run is recorded on the row
    that is already open, which is where the rotation will happen."""
    records = github_records()
    title = fworker.rotation_title(2284, "github",
                                   [records[0]["value_sha256_12"]])
    pages, calls = alarm(tmp_path, monkeypatch, 2291, records,
                         add_result=WqResult(1, duplicate_out(2310, title)))
    assert [c[0] for c in calls] == ["add", "note"]
    assert calls[0][calls[0].index("--title") + 1] == title   # same row, same title
    assert calls[1][1] == "2310"                              # noted on the open row
    note = calls[1][2]
    assert "also fired on wq#2291 attempt 1" in note
    hits_path = os.path.join(str(tmp_path), "wq2291", "mask-hits-1.json")
    assert hits_path in note            # this run's forensics, not the first run's
    with open(hits_path) as fh:
        assert json.load(fh)[0]["prefix"] == "ghp_"
    assert pages and pages[0][1] == "high"   # a burn still pages every time


def test_an_add_refused_for_any_other_reason_is_loud(tmp_path, monkeypatch):
    """Only a DUPLICATE carrying a row id is a dedupe. Anything else — a refused lane,
    a gate, a drifted message — is a rotation row that did NOT get filed, and has to
    say so rather than pass for a collapsed re-fire."""
    _, calls = alarm(tmp_path, monkeypatch, 2291, github_records(),
                     add_result=WqResult(1, "REFUSED: lane 'fleet' is not configured"))
    assert [c[0] for c in calls] == ["add"]           # no note: nothing to note on


@pytest.mark.skipif(not os.path.exists(fworker.WQ_BIN), reason="wq not on this host")
def test_the_dedupe_is_scoped_to_rows_that_are_not_done():
    """The hazard a value-keyed title carries: a credential that leaks AGAIN after it
    was rotated must page again, and a key that remembers forever would swallow it.
    Nothing in fworker decides that — `wq add` compares a new title against rows with
    `status != 'done'` only, so a CLOSED rotation row is not a match and the next leak
    of that value files a fresh pri-1. This pins the property the title shape leans on:
    if wq ever dedupes over the whole table, this fails instead of a burn going quiet."""
    with open(fworker.WQ_BIN, errors="replace") as fh:
        source = fh.read()
    assert "FROM tasks WHERE status!='done'" in source


def test_an_alarm_of_nothing_but_the_mask_literal_files_nothing(tmp_path, monkeypatch):
    """wq#2339's triage recipe, run at the alarm instead of on the row it filed. Every
    value here is the masker's own output read back — the synthetic-fixture shape that
    fired three rows during wq#2298's own run — and a value that has already been
    replaced by the mask literal cannot be a credential."""
    record = dict(github_records()[0],
                  value_sha256_12=masking.value_sha12(masking.MASK))
    pages, calls = alarm(tmp_path, monkeypatch, 2298, [record])
    assert calls == [] and pages == []
    # the forensics still land: the judgement never decides what evidence is kept
    with open(os.path.join(str(tmp_path), "wq2298", "mask-hits-1.json")) as fh:
        assert json.load(fh)[0]["value_sha256_12"] == masking.value_sha12(masking.MASK)


def test_one_value_the_list_cannot_explain_keeps_the_alarm(tmp_path, monkeypatch):
    """All-or-nothing on purpose: a real token sharing a line with masked output is
    still a burn, and a page held back is the failure the whole path exists to avoid."""
    records = github_records()
    masked = dict(records[0], value_sha256_12=masking.value_sha12(masking.MASK))
    pages, calls = alarm(tmp_path, monkeypatch, 2298, [masked] + records)
    assert [c[0] for c in calls] == ["add"]
    assert pages and pages[0][1] == "high"


def test_the_self_output_hashes_are_the_ones_the_storm_named():
    """wq#2339 read these three out of the storm's own mask-hits files. Two follow MASK
    (a self-match records the body after the kept prefix); the third is the pre-wq#2300
    sk- backtrack tail, whose truncated body no literal here can rebuild."""
    assert masking.SELF_OUTPUT_SHA12 >= {"20227a136ec3", "2c9e04df04aa",
                                         "f50f2d7b79dd"}
    assert masking.value_sha12(masking.MASK) == "20227a136ec3"
    # scan()'s record and the dedupe key have to be the same 12 hex, or a hash printed
    # on a row cannot be compared against a candidate at all (wq#2288)
    body = GHP[len("ghp_"):]
    assert github_records()[0]["value_sha256_12"] == masking.value_sha12(body)


# --- wq#2327: a rule narrowed mid-attempt must reach the attempts in flight ---------

# The rule exactly as it was deployed when it filed wq#2327 at 22:44:02, appended to a
# COPY of masking.py — a second table inside one process is the only way to hold "what
# this process imported" and "what is on disk now" apart in a test.
LEGACY_KV = """

PATTERNS = PATTERNS + [("kv-secret", re.compile(
    r'((TOKEN|KEY|SECRET|PASSWORD|PASSWD|PAT)["\\s]*[:=]["\\s]*)[A-Za-z0-9._-]{12,}',
    re.IGNORECASE), r"\\1MASKED")]
"""

# wq#2327's burned "credential": a 19-char function name (node_render_gateway.py:418,
# in 11 tracked files), read as a secret by the legacy rule's missing tail boundary.
WQ2327_LINE = "        key = apply_key_allowlist(reg)"


def masking_copy(tmp_path, extra=""):
    """A loadable copy of masking.py at our own path, optionally with `extra` source
    appended. Copying is what makes `deployed()` load: an unchanged file is returned
    as the imported module, by identity, without a second exec."""
    with open(masking.__file__, errors="replace") as fh:
        source = fh.read()
    path = tmp_path / "masking_copy.py"
    path.write_text(source + extra)
    return str(path)


def test_deployed_is_the_imported_module_when_the_file_has_not_changed():
    assert masking.deployed() is masking


def test_deployed_loads_the_table_on_disk_not_the_imported_one(tmp_path):
    path = masking_copy(tmp_path, LEGACY_KV)
    live = masking.deployed(path)
    assert live is not masking
    assert live.mask_text(WQ2327_LINE)[1] == ["kv-secret"]   # the old table still fires
    assert masking.mask_text(WQ2327_LINE)[1] == []           # today's does not
    # cached on (path, mtime, size): unchanged is free, rewritten reloads
    assert masking.deployed(path) is live
    redeployed = masking_copy(tmp_path, LEGACY_KV + "\n# a later deploy\n")
    assert masking.deployed(redeployed) is not live


def test_a_deployed_file_that_stopped_masking_is_not_trusted(tmp_path):
    """Silencing the tripwire is worse than a false page, so a file that no longer
    labels the canary — an emptied table, a half-written deploy — is refused and the
    caller keeps the table it imported."""
    for extra in ("\nPATTERNS = []\n", "\nPATTERNS = [   # truncated deploy\n"):
        assert masking.deployed(masking_copy(tmp_path, extra)) is masking


def test_a_rule_narrowed_mid_attempt_stops_the_alarm(tmp_path, monkeypatch):
    """wq#2327, the defect this closes. wq#2297's attempt started 22:37:43 and scored
    every line for the next 56 minutes with the table it imported then. The kv rule was
    narrowed at 22:54/23:31, the chain guard landed 22:57, the provenance screen 23:09
    — and that attempt still filed wq#2327 (22:44), wq#2356 (23:11) and wq#2366 (23:25),
    all three under the retired rule, none of them a credential.
    """
    alarms = []
    monkeypatch.setattr(fworker, "mask_alarm",
                        lambda tid, att, labels, records=None: alarms.append(labels))
    real, copy = masking.deployed, masking_copy(tmp_path)
    monkeypatch.setattr(masking, "deployed", lambda path=None: real(copy))
    # `hits` is what the stale in-process table reported; the file on disk no longer
    # reads the line as anything, so nothing pages and nothing is filed.
    fworker.screen_mask_hits(["kv-secret"], WQ2327_LINE, set(), set(), set(),
                             [os.path.dirname(masking.__file__)], 2327, 1)
    assert alarms == []


def test_a_hit_the_deployed_table_still_reads_pages_as_before(tmp_path, monkeypatch):
    """The other direction: the recheck must not become a way for a mask hit to go
    quiet. Same line, same stale caller — but the deployed table fires on it too."""
    alarms = []
    monkeypatch.setattr(fworker, "mask_alarm",
                        lambda tid, att, labels, records=None: alarms.append(labels))
    real, copy = masking.deployed, masking_copy(tmp_path, LEGACY_KV)
    monkeypatch.setattr(masking, "deployed", lambda path=None: real(copy))
    fworker.screen_mask_hits(["kv-secret"], WQ2327_LINE, set(), set(), set(),
                             [os.path.dirname(masking.__file__)], 2327, 1)
    assert alarms == [["kv-secret"]]


def test_a_dropped_label_can_still_alarm_later_in_the_attempt(tmp_path, monkeypatch):
    """Dropping is per line, not per attempt. `alarmed` is updated only for labels
    that survive the recheck, so a label quieted on a line of source still pages on
    the real credential forty turns later — the wq#2290 bookkeeping rule, kept."""
    alarms, alarmed = [], set()
    monkeypatch.setattr(fworker, "mask_alarm",
                        lambda tid, att, labels, records=None: alarms.append(labels))
    real, copy = masking.deployed, masking_copy(tmp_path)
    monkeypatch.setattr(masking, "deployed", lambda path=None: real(copy))
    repo = [os.path.dirname(masking.__file__)]
    fworker.screen_mask_hits(["kv-secret"], WQ2327_LINE, alarmed, set(), set(),
                             repo, 2327, 1)
    assert (alarms, alarmed) == ([], set())
    # `live` short-circuits provenance, so the second line needs no git grep
    fworker.screen_mask_hits(["kv-secret"], f"password: {PW}", alarmed, set(), {PW},
                             repo, 2327, 1)
    assert alarms == [["kv-secret"]] and alarmed == {"kv-secret"}


# --- wq#2393: a line of the pipe is not the model's context ------------------
# The screen used to take the whole raw line, and the pipe is wider than the stream:
# run_child merges the child's stderr into it, and a real frame's envelope carries
# fields the CLI writes for itself. wq#2253 paged pri-1 rotate-today on text in
# neither — one line of the doctrine log that its read window began one line below.


def _screen(raw, alarms, facing=None, alarmed=None):
    """One trip through the read loop's screen. `live` carries PW so provenance
    short-circuits and no test here shells out to git."""
    kwargs = {"facing": facing} if facing else {}
    fworker.screen_stream_hits(masking.mask_text(raw)[1], raw,
                               set() if alarmed is None else alarmed, set(), {PW},
                               [os.path.dirname(masking.__file__)], 2393, 1, **kwargs)
    return alarms


@pytest.fixture
def alarms(monkeypatch):
    fired = []
    monkeypatch.setattr(fworker, "mask_alarm",
                        lambda tid, att, labels, records=None: fired.append(labels))
    return fired


def test_a_credential_in_a_tool_result_still_pages(alarms):
    """The wq#2311 shape, unchanged: a command printed it, the frame handed it to the
    model, the CLI has no lever to take it back. This is the case the tripwire is for
    and the scoping must not touch it."""
    frame = {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "content": [{"type": "text",
                                             "text": f"password: {PW}"}]}]}}
    assert _screen(json.dumps(frame), alarms) == [["kv-secret"]]


def test_the_same_value_in_the_frame_envelope_does_not_page(alarms):
    """`toolUseResult` is the CLI's own record of what a tool did — it sits beside the
    message on the same physical line and the model is handed none of it. Same value,
    same line, same label: masked either way, filed only for the message."""
    raw = json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "The file has been updated."}]},
        "toolUseResult": {"structuredPatch": f"password: {PW}"}})
    assert _screen(raw, alarms) == []
    assert PW not in masking.mask_text(raw)[0]   # still redacted in every artefact


def test_a_line_a_hook_printed_to_stderr_does_not_page(alarms):
    """run_child's Popen merges stderr into stdout, so a guard message, a warning, or
    anything a hook's own subprocess leaks arrives on the same descriptor as a frame.
    None of it parses as one, and none of it reached the model."""
    assert _screen(f"guard.sh: refusing to print password: {PW}", alarms) == []


def test_an_init_envelope_does_not_page(alarms):
    """The one frame that is all bookkeeping — cwd, tool list, mcp servers, key
    source. The model gets a system prompt, never this."""
    raw = json.dumps({"type": "system", "subtype": "init",
                      "mcp_servers": [{"env": f"password: {PW}"}]})
    assert _screen(raw, alarms) == []


def test_a_gateway_output_item_pages_but_its_sidecar_does_not(alarms):
    """Same rule on the despag path: `response.output` is the turn, the run_state
    sidecar beside it is despag's own bookkeeping. The whole output list counts, not
    just the two item types _frames_from_lumen_output maps — a message item is the
    model's own text, and scoring only what that mapper needs would lose it."""
    item = {"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": f"password: {PW}"}]}
    ev = {"type": "response.completed", "response": {"output": [item]}}
    sidecar = {"type": "response.completed",
               "response": {"output": [], "lumen": {"run_state": {
                   "result_text": f"password: {PW}"}}}}
    assert _screen(json.dumps(sidecar), alarms,
                   facing=fworker.gateway_facing_text) == []
    assert _screen(json.dumps(ev), alarms,
                   facing=fworker.gateway_facing_text) == [["kv-secret"]]


def test_a_label_the_envelope_armed_does_not_consume_the_attempts_alarm(alarms):
    """The wq#2290 bookkeeping rule, kept across the narrowing: a label dropped for
    being out of context must stay unalarmed, so the real burn forty turns later still
    pages. Dropping it into `alarmed` would silently swallow that one."""
    alarmed = set()
    envelope = json.dumps({"type": "user", "message": {"content": []},
                           "toolUseResult": f"password: {PW}"})
    assert _screen(envelope, alarms, alarmed=alarmed) == [] and alarmed == set()
    frame = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": f"password: {PW}"}]}}
    assert _screen(json.dumps(frame), alarms, alarmed=alarmed) == [["kv-secret"]]
    assert alarmed == {"kv-secret"}
