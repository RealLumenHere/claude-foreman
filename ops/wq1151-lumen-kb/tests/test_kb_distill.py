"""Distiller tests (wq#1151 P3) — LiteLLM mocked, real tmp git vault."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kb_distill  # noqa: E402


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    v = tmp_path / "kb"
    v.mkdir()
    subprocess.run(["git", "-C", str(v), "init", "-q"], check=True)
    for k, val in (("user.email", "kb@test"), ("user.name", "kb-test")):
        subprocess.run(["git", "-C", str(v), "config", k, val], check=True)
    monkeypatch.setattr(kb_distill, "VAULT", v)
    return v


@pytest.fixture()
def fake_llm(monkeypatch):
    calls = []

    def _chat(messages, model, max_tokens=2000):
        calls.append({"messages": messages, "model": model})
        return "One-liner.\n\n## Facts\n- nest uses 24V"

    monkeypatch.setattr(kb_distill, "_chat", _chat)
    return calls


def test_distill_writes_and_commits(vault, fake_llm, tmp_path):
    t = tmp_path / "session.md"
    t.write_text("frank: the nest thermostat needs the C wire\n")
    rel = kb_distill.distill("google-nest", t, "cwire", [], dry_run=False)
    assert rel.startswith("google-nest/") and rel.endswith("-cwire.md")
    body = (vault / rel).read_text()
    assert "nest uses 24V" in body and "distilled" in body
    log = subprocess.run(["git", "-C", str(vault), "log", "--oneline"],
                         capture_output=True, text=True, check=True).stdout
    assert f"kb: distill {rel}" in log


def test_dry_run_writes_nothing(vault, fake_llm, tmp_path):
    t = tmp_path / "s.md"
    t.write_text("hello")
    out = kb_distill.distill("google-nest", t, "x", [], dry_run=True)
    assert "## Facts" in out
    assert not (vault / "google-nest").exists()


def test_jsonl_extraction(tmp_path):
    t = tmp_path / "s.jsonl"
    lines = [
        json.dumps({"message": {"role": "user", "content": "plain string"}}),
        json.dumps({"message": {"role": "assistant", "content": [
            {"type": "text", "text": "block text"},
            {"type": "tool_use", "name": "Bash"},
        ]}}),
        "not json",
    ]
    t.write_text("\n".join(lines))
    text = kb_distill.load_transcript(t)
    assert "user: plain string" in text and "assistant: block text" in text
    assert "Bash" not in text


def test_empty_transcript_fails(vault, fake_llm, tmp_path):
    t = tmp_path / "empty.md"
    t.write_text("  \n")
    with pytest.raises(SystemExit):
        kb_distill.distill("google-nest", t, "x", [], dry_run=True)


def test_traversal_topic_rejected(vault, fake_llm, tmp_path):
    t = tmp_path / "s.md"
    t.write_text("data")
    with pytest.raises(Exception):
        kb_distill.distill("../evil", t, "x", [], dry_run=False)
