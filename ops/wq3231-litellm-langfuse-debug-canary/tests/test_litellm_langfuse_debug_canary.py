import importlib.util
import os
from importlib.machinery import SourceFileLoader

import pytest

_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "litellm-langfuse-debug-canary",
)
_loader = SourceFileLoader("litellm_langfuse_debug_canary", _MODULE_PATH)
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
canary = importlib.util.module_from_spec(_spec)
_loader.exec_module(canary)


def test_build_request_shapes_covers_ticket_candidate_causes():
    shapes = canary.build_request_shapes()
    names = {s["name"] for s in shapes}
    # wq#3231's own candidate list: ordinary callers, a media/multimodal path, and an
    # oversized-field path (the _truncate_item_in_place size-limit candidate).
    assert {"messenger", "fleet-worker", "judge", "multimodal", "oversized-metadata"} <= names
    multimodal = next(s for s in shapes if s["name"] == "multimodal")
    assert any(
        isinstance(block, dict) and block.get("type") == "image_url"
        for msg in multimodal["messages"]
        for block in (msg["content"] if isinstance(msg["content"], list) else [])
    )
    oversized = next(s for s in shapes if s["name"] == "oversized-metadata")
    assert len(oversized["metadata"]["probe_payload"]) > 1_000_000


def test_build_canary_launch_cmd_never_touches_prod_container():
    cmd = canary.build_canary_launch_cmd(
        image="ghcr.io/example/litellm:1.83.10",
        config_path="/home/lumen/docker/litellm/litellm_config.yaml",
        env_path="/home/lumen/docker/litellm/.env",
    )
    assert cmd[0] == "docker"
    assert "run" in cmd
    assert "--rm" in cmd
    # a throwaway name, never the prod container's own name
    assert "litellm-langfuse-debug-canary" in cmd
    assert "-e" in cmd and "LANGFUSE_DEBUG=true" in cmd
    assert "LITELLM_LOG=DEBUG" in cmd
    assert "--env-file" in cmd and "/home/lumen/docker/litellm/.env" in cmd


def test_build_canary_launch_cmd_requires_real_paths_never_guesses():
    with pytest.raises(ValueError):
        canary.build_canary_launch_cmd(image="", config_path="", env_path="")


def test_build_canary_launch_cmd_respects_custom_runtime_and_port():
    cmd = canary.build_canary_launch_cmd(
        image="img", config_path="/c.yaml", env_path="/e.env",
        container_runtime="podman", port=15000,
    )
    assert cmd[0] == "podman"
    assert "15000:4000" in cmd


# ---------------------------------------------------------------------------
# extract_swallowed_exceptions — the payoff logic, built from the real DEBUG line
# shapes seen both in this fleet's own prior finding (wq#3122's citations into
# langfuse/_task_manager/ingestion_consumer.py) and in upstream langfuse-python issue
# reports (e.g. github.com/langfuse/langfuse#5862's captured debug log), not invented.
# ---------------------------------------------------------------------------

_FIXTURE_LOG_ONE_BATCH = """\
DEBUG:langfuse:consumer is running...
DEBUG:langfuse:~0 items in the Langfuse queue
DEBUG:langfuse:uploading batch of 2 items
DEBUG:langfuse:making request: {"batch": [{"type": "trace-create"}]} to http://langfuse-web:3000/api/public/ingestion
DEBUG:langfuse:Data does not match any variant of untagged enum IngestionEvent at line 1 column 4021
ERROR:langfuse:Unexpected error occurred. Please check your request and contact support: https://langfuse.com/support.
"""

_FIXTURE_LOG_NO_DEBUG_CAUSE = """\
DEBUG:langfuse:uploading batch of 1 items
DEBUG:langfuse:making request: {"batch": []} to http://langfuse-web:3000/api/public/ingestion
ERROR:langfuse:Unexpected error occurred. Please check your request and contact support: https://langfuse.com/support.
"""

_FIXTURE_LOG_TWO_BATCHES = """\
DEBUG:langfuse:uploading batch of 1 items
DEBUG:langfuse:making request: {"batch": [{"id": "a"}]} to http://langfuse-web:3000/api/public/ingestion
DEBUG:langfuse:[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed
ERROR:langfuse:Unexpected error occurred. Please check your request and contact support: https://langfuse.com/support.
DEBUG:langfuse:uploading batch of 1 items
DEBUG:langfuse:making request: {"batch": [{"id": "b"}]} to http://langfuse-web:3000/api/public/ingestion
DEBUG:langfuse:item size 12345678 exceeds limit
ERROR:langfuse:Unexpected error occurred. Please check your request and contact support: https://langfuse.com/support.
"""


def test_extract_finds_the_real_cause_logged_just_before_the_generic_line():
    findings = canary.extract_swallowed_exceptions(_FIXTURE_LOG_ONE_BATCH)
    assert len(findings) == 1
    assert "does not match any variant" in findings[0]["likely_cause"]
    assert "trace-create" in findings[0]["batch_context"]


def test_extract_falls_back_to_none_when_no_debug_line_precedes_it():
    # TEST TO FAILURE (wq#2630): this fixture's marker has no exception-shaped DEBUG
    # line ahead of it (only the batch/request lines) — the extractor must say so
    # plainly rather than fabricate a cause from the wrong line.
    findings = canary.extract_swallowed_exceptions(_FIXTURE_LOG_NO_DEBUG_CAUSE)
    assert len(findings) == 1
    assert findings[0]["likely_cause"] is None
    assert "batch" in findings[0]["batch_context"]


def test_extract_does_not_cross_batch_boundaries_for_multiple_occurrences():
    findings = canary.extract_swallowed_exceptions(_FIXTURE_LOG_TWO_BATCHES)
    assert len(findings) == 2
    assert "CERTIFICATE_VERIFY_FAILED" in findings[0]["likely_cause"]
    assert '"id": "a"' in findings[0]["batch_context"]
    assert "exceeds limit" in findings[1]["likely_cause"]
    assert '"id": "b"' in findings[1]["batch_context"]


def test_extract_returns_empty_list_when_no_marker_present():
    findings = canary.extract_swallowed_exceptions("DEBUG:langfuse:all fine\n")
    assert findings == []


# ops/wq3231-langfuse-debug-capture/langfuse_debug_capture.py's FileHandler formats
# records as "%(asctime)s %(levelname)s %(message)s" (no logger name: that handler is
# only ever attached to the 'langfuse' logger). This extractor has to read that file
# shape too, not just a bare `logging.basicConfig()` stream.
_FIXTURE_CAPTURE_FILE_LOG = """\
2026-10-05 02:14:01,001 DEBUG uploading batch of 1 items
2026-10-05 02:14:01,050 DEBUG making request: {"batch": [{"id": "c"}]} to http://langfuse-web:3000/api/public/ingestion
2026-10-05 02:14:01,090 DEBUG ConnectError('all connection attempts failed')
2026-10-05 02:14:01,095 ERROR Unexpected error occurred. Please check your request and contact support: https://langfuse.com/support.
"""


def test_extract_reads_the_debug_capture_module_file_format():
    findings = canary.extract_swallowed_exceptions(_FIXTURE_CAPTURE_FILE_LOG)
    assert len(findings) == 1
    assert "ConnectError" in findings[0]["likely_cause"]
    assert '"id": "c"' in findings[0]["batch_context"]
