"""wq#5015: every filter is lossless (round-trip), fail-safe (never grows, never raises) and
deterministic; the hook rewrites both OpenAI and Anthropic tool-result shapes."""
import asyncio
import importlib
import os
import random
import string
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tool_result_compress as trc  # noqa: E402

DECODERS = {n: d for n, _, d in trc.FILTERS}


def roundtrip(s):
    out, name = trc.compress_text(s)
    if name is None:
        assert out == s
        return out, name
    assert len(out) < len(s)
    if DECODERS.get(name):
        assert DECODERS[name](out) == s
    return out, name


NUMBERED = "\n".join(f"{i:>6}\tdef f{i}(x):  # line {i}" for i in range(1, 60))
CLAUDE_READ = "\n".join(f"{i:>6}→    return value_{i}" for i in range(1, 60))
GREP = "\n".join(f"harness/{'fworker' if i < 20 else 'wq_close'}.py:{i * 7}:    wq_done(tid, notes)"
                 for i in range(40))
FIND = "\n".join([f"./harness/tests/test_{i}.py" for i in range(30)] +
                 [f"./ops/wq50{i}/install.sh" for i in range(10)] + ["./README.md"])
TREE = "\n".join([".", "├── harness", "│   ├── fworker.py", "│   ├── tests", "│   │   ├── a.py",
                  "│   │   └── b.py", "│   └── wq_close.py", "└── ops",
                  "    ├── wq5014", "    └── wq5015"] * 8 + ["", "4 directories, 5 files"])
DIFF = "\n".join(
    sum([[f"diff --git a/src/m{i}.py b/src/m{i}.py", f"index 1a2b3c{i}..4d5e6f{i} 100644",
          f"--- a/src/m{i}.py", f"+++ b/src/m{i}.py", "@@ -1,3 +1,3 @@", " ctx",
          f"-old {i}", f"+new {i}", " ctx"] for i in range(30)], []))
STATUS = """On branch main
Your branch is up to date with 'origin/main'.

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)
	modified:   judge/JUDGE_MODEL_PIN
	modified:   judge/JUDGE_MODEL_FALLBACK

Untracked files:
  (use "git add <file>..." to include in what will be committed)
	.auto-dispatch.lock
	auto-dispatch.log
""" * 4
LOG = "\n".join(["INFO start"] + ["WARN retrying connection to 127.0.0.1:4000"] * 50 +
                ["INFO done"] * 3 + ["x"] * 2)


@pytest.mark.parametrize("sample,expect", [
    (NUMBERED, "read-numbered"), (CLAUDE_READ, "read-numbered"), (GREP, "grep"),
    (FIND, "paths"), (TREE, "tree"), (DIFF, "git-diff"), (STATUS, "git-status"),
    (LOG, "dedup-log"),
])
def test_each_filter_fires_and_round_trips(sample, expect):
    out, name = roundtrip(sample)
    assert name == expect, (name, out[:200])
    assert out.startswith(f"[rtk:{expect} ")


def test_git_status_keeps_every_state_line():
    out, name = trc.compress_text(STATUS)
    assert name == "git-status"
    for needle in ("On branch main", "modified:   judge/JUDGE_MODEL_PIN", "auto-dispatch.log"):
        assert out.count(needle) == STATUS.count(needle)
    assert '(use "git' not in out.split("\n", 1)[1]


def test_short_text_untouched():
    s = "short tool output"
    assert trc.compress_text(s) == (s, None)


def test_prose_untouched():
    s = ("The function returns early when the lease has expired, because the fence has "
         "moved on. ") * 20
    assert trc.compress_text(s) == (s, None)


def test_never_grows_and_never_raises_on_noise():
    rnd = random.Random(5015)
    alphabet = string.printable + "│├└──→⟨×⟩§"
    for _ in range(400):
        n = rnd.randint(0, 3000)
        s = "".join(rnd.choice(alphabet) for _ in range(n))
        roundtrip(s)


def test_never_grows_on_mutated_structured_inputs():
    rnd = random.Random(42)
    for base in (NUMBERED, GREP, FIND, TREE, DIFF, LOG):
        for _ in range(60):
            lines = base.split("\n")
            for _ in range(rnd.randint(1, 5)):
                i = rnd.randrange(len(lines))
                op = rnd.choice(["dup", "del", "junk", "space"])
                if op == "dup":
                    lines.insert(i, lines[i])
                elif op == "del" and len(lines) > 1:
                    del lines[i]
                elif op == "junk":
                    lines[i] = rnd.choice(["  ", "§", "| x", "a ⟨×3⟩", "/", ""]) + lines[i]
                else:
                    lines[i] = " " + lines[i]
            roundtrip("\n".join(lines))


def test_ambiguous_inputs_are_left_alone():
    assert trc.compress_text(LOG + "\nalready ⟨×4⟩")[1] != "dedup-log"
    assert trc.compress_text(DIFF + "\n§")[1] != "git-diff"


def test_deterministic():
    for s in (NUMBERED, GREP, FIND, TREE, DIFF, STATUS, LOG):
        assert trc.compress_text(s) == trc.compress_text(s)


def test_truncate_is_off_by_default_and_opt_in(monkeypatch):
    big = "".join(random.Random(1).choice(string.ascii_letters + " \n") for _ in range(9000))
    assert trc.compress_text(big) == (big, None)
    monkeypatch.setenv("TOOL_COMPRESS_TRUNCATE_CHARS", "3000")
    out, name = trc.compress_text(big)
    assert name == "smart-truncate" and "LOSSY" in out.split("\n", 1)[0] and len(out) < len(big)


def _hook(data):
    return asyncio.run(trc.proxy_handler_instance.async_pre_call_hook(None, None, data,
                                                                       "completion"))


def test_hook_openai_and_anthropic_shapes():
    data = {"messages": [
        {"role": "user", "content": GREP},  # user text is never touched
        {"role": "tool", "tool_call_id": "a", "content": GREP},
        {"role": "tool", "tool_call_id": "b", "content": [{"type": "text", "text": FIND}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "c", "content": TREE},
            {"type": "tool_result", "tool_use_id": "d",
             "content": [{"type": "text", "text": NUMBERED}]},
            {"type": "text", "text": DIFF}]},
    ]}
    out = _hook(data)["messages"]
    assert out[0]["content"] == GREP
    assert out[1]["content"].startswith("[rtk:grep ")
    assert out[2]["content"][0]["text"].startswith("[rtk:paths ")
    assert out[3]["content"][0]["content"].startswith("[rtk:tree ")
    assert out[3]["content"][1]["content"][0]["text"].startswith("[rtk:read-numbered ")
    assert out[3]["content"][2]["text"] == DIFF


def test_hook_kill_switch_and_per_request_opt_out(monkeypatch):
    d = {"messages": [{"role": "tool", "tool_call_id": "a", "content": GREP}],
         "metadata": {"tool_compress": False}}
    assert _hook(d)["messages"][0]["content"] == GREP
    monkeypatch.setenv("TOOL_COMPRESS_DISABLE", "1")
    d = {"messages": [{"role": "tool", "tool_call_id": "a", "content": GREP}]}
    assert _hook(d)["messages"][0]["content"] == GREP


def test_hook_survives_garbage():
    for data in ({}, {"messages": None}, {"messages": [None, 3, "x", {"role": "tool"}]},
                 {"messages": [{"role": "user", "content": [None, {"type": "tool_result"}]}]}):
        assert _hook(data) is data


UNPADDED = "\n".join(f"{i}\tline {i} of a real file read" for i in range(118, 300))


def test_unpadded_read_is_kept_by_default():
    assert trc.compress_text(UNPADDED) == (UNPADDED, None)


def test_unpadded_read_numbers_dropped_when_opted_in(monkeypatch):
    monkeypatch.setenv("TOOL_COMPRESS_READ_NUMBERS", "drop")
    out, name = roundtrip(UNPADDED)
    assert name == "read-numbered" and "lines 118-299" in out.split("\n", 1)[0]
    assert "\n119\t" not in out


def test_drop_mode_refuses_gaps(monkeypatch):
    monkeypatch.setenv("TOOL_COMPRESS_READ_NUMBERS", "drop")
    gap = UNPADDED.replace("\n150\t", "\n151\t", 1)
    out, name = roundtrip(gap)
    assert name != "read-numbered" or "omitted" not in out.split("\n", 1)[0]
