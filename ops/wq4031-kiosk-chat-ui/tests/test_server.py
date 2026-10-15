import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server
from fastapi.testclient import TestClient


def test_index_serves_kiosk_html():
    client = TestClient(server.app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"chat-form" in resp.content


def test_api_chat_returns_llm_reply(monkeypatch):
    async def fake_chat_completion(message: str) -> str:
        assert message == "hello"
        return "hi there"

    monkeypatch.setattr(server, "chat_completion", fake_chat_completion)
    client = TestClient(server.app)
    resp = client.post("/api/chat", json={"message": "hello"})
    assert resp.status_code == 200
    assert resp.json() == {"reply": "hi there"}


def test_api_chat_rejects_empty_message():
    client = TestClient(server.app)
    resp = client.post("/api/chat", json={"message": "   "})
    assert resp.status_code == 400


def test_api_camera_snapshot_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "SNAPSHOT_DIR", tmp_path)
    client = TestClient(server.app)
    resp = client.post(
        "/api/camera/snapshot",
        files={"file": ("snapshot.jpg", b"\xff\xd8\xff\xd9", "image/jpeg")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    saved = tmp_path / os.path.basename(body["path"])
    assert saved.read_bytes() == b"\xff\xd8\xff\xd9"


def test_api_tts_503_when_voice_venv_missing(monkeypatch):
    def fake_get_tts():
        raise ImportError("kokoro_onnx not installed")

    monkeypatch.setattr(server, "get_tts", fake_get_tts)
    client = TestClient(server.app)
    resp = client.get("/api/tts", params={"text": "hello"})
    assert resp.status_code == 503
