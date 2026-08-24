"""crush.py test — wq#888.

Zero quota. The three properties research/SMARTCRUSHER_HOOK.md leans on are
asserted here directly, because each one is what makes the filter safe to put in
front of a worker's Bash pipeline: it is lossless, it never makes a payload
bigger, and it refuses rather than guesses when the encoding would be ambiguous.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import crush

CRUSH = os.path.join(os.path.dirname(__file__), "..", "crush.py")


def roundtrip(value):
    text = json.dumps(value)
    out, stats = crush.crush_text(text)
    if stats["kind"] == "passthrough":
        return value, stats
    return crush.decode(json.loads(out)), stats


# ---- lossless ----

def test_homogeneous_array_roundtrips_and_shrinks():
    value = [{"id": i, "lane": "foreman-tok", "title": f"ticket {i}"} for i in range(20)]
    back, stats = roundtrip(value)
    assert back == value
    assert stats["kind"] == "json"
    assert stats["out"] < stats["in"]


def test_nested_arrays_roundtrip():
    value = {"runs": [{"a": 1, "b": [{"x": 1, "y": 2}, {"x": 3, "y": 4}]},
                      {"a": 2, "b": [{"x": 5, "y": 6}, {"x": 7, "y": 8}]}]}
    assert roundtrip(value)[0] == value


def test_scalar_and_mixed_types_roundtrip():
    value = [{"a": None, "b": True, "c": 1.5, "d": "x", "e": [1, 2], "f": {"g": 1}},
             {"a": None, "b": False, "c": 2.5, "d": "y", "e": [3], "f": {"g": 2}}]
    assert roundtrip(value)[0] == value


def test_all_columns_constant_collapses_but_roundtrips():
    value = [{"lane": "a", "state": "open"}] * 5
    back, stats = roundtrip(value)
    assert back == value
    assert stats["out"] < stats["in"]


def test_unicode_survives():
    value = [{"k": "café → ✓"}, {"k": "naïve → ✗"}]
    assert roundtrip(value)[0] == value


# ---- never worse ----

def test_plain_text_passes_through_byte_identical():
    text = "total 48\ndrwxr-xr-x 2 lumen lumen 4096 Aug 24 01:33 .\n"
    out, stats = crush.crush_text(text)
    assert out == text
    assert stats["kind"] == "passthrough"


def test_empty_and_whitespace_pass_through():
    for text in ("", "   \n\n"):
        assert crush.crush_text(text)[0] == text


def test_already_minified_small_json_is_not_inflated():
    text = '{"busy":false}'
    out, _ = crush.crush_text(text)
    assert len(out) <= len(text)


def test_heterogeneous_array_is_not_columnarized():
    # Differing key sets: encoding these would need an absence sentinel, and a
    # sentinel that could collide with real data is exactly what the encoding
    # refuses to introduce.
    value = [{"a": 1}, {"a": 2, "b": 3}]
    text = json.dumps(value)
    out, _ = crush.crush_text(text)
    assert crush.MARKER not in out
    assert json.loads(out) == value


def test_array_of_scalars_is_not_columnarized():
    value = [1, 2, 3, 4]
    out, _ = crush.crush_text(json.dumps(value))
    assert json.loads(out) == value


# ---- bail, don't guess ----

def test_payload_containing_the_marker_is_returned_untouched():
    text = json.dumps([{"__crush__": "x", "a": 1}, {"__crush__": "y", "a": 2}])
    out, stats = crush.crush_text(text)
    assert out == text
    assert stats["kind"] == "passthrough"


def test_jsonl_is_treated_as_an_array():
    lines = "\n".join(json.dumps({"ts": i, "lane": "foreman-tok"}) for i in range(10))
    out, stats = crush.crush_text(lines)
    assert stats["kind"] == "jsonl"
    assert crush.decode(json.loads(out)) == [{"ts": i, "lane": "foreman-tok"} for i in range(10)]


def test_json_followed_by_prose_is_not_mistaken_for_jsonl():
    # The one way this filter could lose data: reading a trailing log line as a
    # failed JSONL record and dropping it.
    text = '{"a":1}\nwarning: something happened\n'
    out, stats = crush.crush_text(text)
    assert out == text
    assert stats["kind"] == "passthrough"


def test_single_json_line_is_not_jsonl():
    text = '{"a":1}'
    assert crush.crush_text(text)[1]["kind"] in ("passthrough", "json")


# ---- CLI, as the hook would invoke it ----

def cli(args, stdin):
    return subprocess.run([sys.executable, CRUSH] + args, input=stdin,
                          capture_output=True, text=True)


def test_cli_filter_roundtrips_through_decode():
    value = [{"id": i, "lane": "foreman-tok"} for i in range(30)]
    crushed = cli([], json.dumps(value))
    assert crushed.returncode == 0
    back = cli(["--decode"], crushed.stdout)
    assert json.loads(back.stdout) == value


def test_cli_max_bytes_caps_after_compressing():
    # Notes vary, so nothing hoists into `const` and the payload is still over
    # the cap after compressing -- the cap then applies to the compressed bytes.
    # That ordering is the point, and the assertion states it as the thing that
    # actually matters: capping compressed bytes carries more records past the
    # same cap than capping raw bytes does.
    value = [{"id": i, "lane": "foreman-tok", "note": f"note-{i}-" + "x" * 30}
             for i in range(50)]
    raw = json.dumps(value)
    out = cli(["--max-bytes", "500"], raw)
    assert len(out.stdout) == 500
    reached = lambda s: max(i for i in range(50) if f'"note-{i}-' in s)
    assert reached(out.stdout) > reached(raw[:500])


def test_cli_stats_go_to_stderr_not_stdout():
    value = [{"id": i, "lane": "a"} for i in range(10)]
    r = cli(["--stats"], json.dumps(value))
    assert json.loads(r.stderr)["kind"] == "json"
    assert crush.MARKER in r.stdout


def test_cli_leaves_non_json_untouched():
    text = "drwxr-xr-x 2 lumen lumen 4096 Aug 24 .\n"
    assert cli([], text).stdout == text
