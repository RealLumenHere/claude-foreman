"""wq#1809: unit tests for despag/uhp/translate.py — the canonical-frame -> UHP
response.* translator. The black-box HTTP/SSE surface is covered by
despag/reports/ (the conformance suite); these pin down the event/assembly
invariants streaming.md actually requires (gapless sequence_number, exactly one
terminal, cancelled -> response.failed+status:cancelled, usage null when unknown,
tool_use/tool_result, stream vs non-stream parity) against fixtures, not a live CLI.
"""
from __future__ import annotations

from despag.uhp.translate import RespTranslator


def _translator(**overrides) -> RespTranslator:
    kwargs = dict(resp_id="resp_test", model="claude-test", previous_response_id=None,
                  store=True, created_at=1700000000, session_id="hsess_test", ignored_fields=None)
    kwargs.update(overrides)
    return RespTranslator(**kwargs)


def _seqs(evs: list[dict]) -> list[int]:
    return [e["sequence_number"] for e in evs]


def test_sequence_number_is_gapless_from_zero_across_start_feed_and_complete():
    tr = _translator()
    evs = list(tr.start())
    evs += tr.feed({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}})
    evs += tr.complete("completed")
    assert _seqs(evs) == list(range(len(evs)))


def test_first_event_is_response_created():
    tr = _translator()
    evs = tr.start()
    assert evs[0]["type"] == "response.created"
    assert evs[0]["response"]["status"] == "in_progress"
    assert evs[0]["response"]["output"] == []


def test_exactly_one_terminal_event_and_it_is_last():
    tr = _translator()
    evs = list(tr.start())
    evs += tr.feed({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}})
    evs += tr.complete("completed")
    terminals = {"response.completed", "response.incomplete", "response.failed"}
    finals = [e for e in evs if e["type"] in terminals]
    assert len(finals) == 1
    assert evs[-1]["type"] in terminals


def test_text_delta_then_output_item_done_with_full_text():
    tr = _translator()
    tr.start()
    deltas = tr.feed({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello, "}]}})
    deltas += tr.feed({"type": "assistant", "message": {"content": [{"type": "text", "text": "world"}]}})
    assert [e["type"] for e in deltas if e["type"] == "response.output_text.delta"] == [
        "response.output_text.delta", "response.output_text.delta"]
    term = tr.complete("completed")
    response = term[-1]["response"]
    assert response["output"] == [
        {"id": response["output"][0]["id"], "type": "message", "status": "completed", "role": "assistant",
         "content": [{"type": "output_text", "text": "Hello, world"}]}
    ]


def test_output_item_added_precedes_every_event_about_it_and_done_follows_all():
    tr = _translator()
    tr.start()
    evs = tr.feed({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}})
    evs += tr.complete("completed")
    added_idx = next(i for i, e in enumerate(evs) if e["type"] == "response.output_item.added")
    done_idx = next(i for i, e in enumerate(evs) if e["type"] == "response.output_item.done")
    item_evs = [i for i, e in enumerate(evs) if e.get("item_id") or e["type"].startswith("response.output_item")]
    assert added_idx == min(item_evs)
    assert done_idx == max(item_evs)


def test_reasoning_summary_events():
    tr = _translator()
    tr.start()
    evs = tr.feed({"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "hmm"}]}})
    assert [e["type"] for e in evs] == [
        "response.output_item.added", "response.reasoning_summary_part.added",
        "response.reasoning_summary_text.delta"]
    term = tr.complete("completed")
    item = term[-1]["response"]["output"][0]
    assert item["type"] == "reasoning"
    assert item["summary"] == [{"type": "summary_text", "text": "hmm"}]


def test_tool_use_emits_function_call_arguments_delta_and_done():
    tr = _translator()
    tr.start()
    evs = tr.feed({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}}]}})
    types = [e["type"] for e in evs]
    assert "response.function_call_arguments.delta" in types
    assert "response.function_call_arguments.done" in types
    done = next(e for e in evs if e["type"] == "response.function_call_arguments.done")
    assert done["arguments"] == '{"command": "ls"}'
    term = tr.complete("completed")
    item = term[-1]["response"]["output"][0]
    assert item["type"] == "function_call"
    assert item["call_id"] == "toolu_1"
    assert item["name"] == "Bash"


def test_tool_result_emits_function_call_output_item():
    tr = _translator()
    tr.start()
    tr.feed({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}}]}})
    evs = tr.feed({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "total 0", "is_error": False}]}})
    assert [e["type"] for e in evs] == ["response.output_item.added", "response.output_item.done"]
    term = tr.complete("completed")
    output = term[-1]["response"]["output"]
    assert output[-1] == {"id": output[-1]["id"], "type": "function_call_output",
                           "call_id": "toolu_1", "output": "total 0", "status": "completed"}


def test_usage_is_null_when_the_result_frame_carries_none():
    tr = _translator()
    tr.start()
    tr.feed({"type": "result", "subtype": "success", "is_error": False, "result": "ok"})
    term = tr.complete("completed")
    assert term[-1]["response"]["usage"] is None


def test_usage_is_never_a_fabricated_zero_but_reports_real_counts():
    tr = _translator()
    tr.start()
    tr.feed({"type": "result", "subtype": "success", "is_error": False, "result": "ok",
             "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 40}})
    term = tr.complete("completed")
    usage = term[-1]["response"]["usage"]
    assert usage["output_tokens"] == 20
    assert usage["cache_read_tokens"] == 40
    assert usage["total_tokens"] == usage["input_tokens"] + 20


def test_cancelled_terminal_is_response_failed_event_with_status_cancelled():
    tr = _translator()
    tr.start()
    tr.feed({"type": "assistant", "message": {"content": [{"type": "text", "text": "partial"}]}})
    evs = tr.complete("cancelled", error={"type": "invalid_request_error", "code": "cancelled",
                                           "message": "cancelled by client"})
    term = evs[-1]
    assert term["type"] == "response.failed"          # never a response.cancelled event type
    assert term["response"]["status"] == "cancelled"   # the status field is authoritative
    assert term["response"]["output"][0]["content"][0]["text"] == "partial"  # partial output retained


def test_fail_emits_nonfatal_error_event_immediately_followed_by_terminal():
    tr = _translator()
    tr.start()
    evs = tr.fail("no frame for 900s (frame-gap watchdog)")
    assert [e["type"] for e in evs] == ["error", "response.failed"]
    assert evs[-1]["response"]["status"] == "failed"
    assert evs[-1]["response"]["error"]["message"] == "no frame for 900s (frame-gap watchdog)"


def test_metadata_session_id_always_present_and_ignored_fields_only_when_sent():
    tr = _translator(ignored_fields=["tools", "include"])
    term = tr.complete("completed")
    meta = term[-1]["response"]["metadata"]
    assert meta["session_id"] == "hsess_test"
    assert meta["ignored_fields"] == ["tools", "include"]

    tr2 = _translator(ignored_fields=None)
    term2 = tr2.complete("completed")
    assert "ignored_fields" not in term2[-1]["response"]["metadata"]


def test_stream_and_non_stream_paths_assemble_identical_output_from_the_same_frames():
    """streaming.md §6 / S-08: replaying the same frames through feed()+complete()
    must produce the same `output`/`usage` whether or not a caller ever looked at the
    intermediate events — the non-stream path discards them, the SSE path streams
    them, both read the final `response` off the same `_response_obj`."""
    frames = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Sum is "}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "42"}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "Sum is 42",
         "usage": {"input_tokens": 10, "output_tokens": 5}},
    ]

    streamed = _translator()
    streamed.start()
    for f in frames:
        streamed.feed(f)
    stream_response = streamed.complete("completed")[-1]["response"]

    blocking = _translator()
    for f in frames:
        blocking.feed(f)   # never called start(); a poll/blocking path need not stream response.created
    blocking_response = blocking.complete("completed")[-1]["response"]

    strip_id = lambda output: [{k: v for k, v in item.items() if k != "id"} for item in output]
    assert strip_id(stream_response["output"]) == strip_id(blocking_response["output"])
    assert stream_response["usage"] == blocking_response["usage"]
    assert stream_response["status"] == blocking_response["status"] == "completed"
