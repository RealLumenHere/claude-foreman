"""wq#4101 — probe_think_rung against a local stub gateway: all three verdicts,
including the FAIL the probe exists to catch (empty content + finish_reason=stop),
so the probe is demonstrably able to fail (wq#2630)."""
import http.server
import json
import pathlib
import sys
import threading

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import probe_think_rung as probe  # noqa: E402


def _response(content, finish_reason, reasoning=None):
    msg = {"role": "assistant", "content": content}
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    return {"choices": [{"message": msg, "finish_reason": finish_reason}],
            "usage": {"completion_tokens": 200}}


class _Stub(http.server.BaseHTTPRequestHandler):
    payload = None

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.dumps(self.payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture()
def stub():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


@pytest.fixture()
def key_file(tmp_path):
    f = tmp_path / "key"
    f.write_text("none\n")
    return str(f)


def _run(stub, key_file, payload):
    _Stub.payload = payload
    return probe.main(["--base", f"http://127.0.0.1:{stub.server_address[1]}",
                       "--key-file", key_file, "--model", "lumen/code"])


def test_pass_on_content(stub, key_file):
    assert _run(stub, key_file, _response("s[::-1]", "stop")) == 0


def test_degraded_pass_on_visible_truncation(stub, key_file):
    assert _run(stub, key_file, _response("", "length", reasoning="thinking...")) == 0


def test_fail_on_wq4101_class(stub, key_file):
    # the silent failure: thinking ate the budget, truncation masked as stop
    assert _run(stub, key_file, _response("", "stop")) == 1


def test_error_on_missing_key_file(stub):
    assert _run(stub, "/nonexistent/key/file", _response("x", "stop")) == 2
