"""wq#5211: fworker_sdk agent loop, tools, permission rules, hooks. No network: the
model call is replaced by a scripted sequence of responses."""
import json
import os
import stat
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


class Blk(types.SimpleNamespace):
    def model_dump(self, exclude_none=False):
        return {k: v for k, v in vars(self).items() if not (exclude_none and v is None)}


def text(t):
    return Blk(type="text", text=t)


def tool(name, inp, tid):
    return Blk(type="tool_use", id=tid, name=name, input=inp)


def msg(blocks, stop="end_turn"):
    return types.SimpleNamespace(id="msg_x", content=blocks, stop_reason=stop,
                                 usage={"input_tokens": 1000, "output_tokens": 100,
                                        "cache_read_input_tokens": 0,
                                        "cache_creation_input_tokens": 0})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FOREMAN_API_CREDIT_DIR", str(tmp_path / "ac"))
    monkeypatch.setenv("FWORKER_SDK_SESSIONS", str(tmp_path / "sessions"))
    monkeypatch.setenv("FWORKER_SDK_API_KEY", "fake-" + "k" * 12)  # not a credential shape
    monkeypatch.setenv("FWORKER_SDK_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("BASH_MAX_OUTPUT_LENGTH", "2000")
    import importlib
    import api_credit
    importlib.reload(api_credit)
    key = tmp_path / "key"
    key.write_text("fake-key\n")
    os.makedirs(api_credit.STATE_DIR, exist_ok=True)
    with open(api_credit.CONFIG_PATH, "w") as f:
        json.dump({"enabled": True, "key_file": str(key), "grant_schedule": [{"from": "2020-01-13", "grant_usd": 200}]}, f)
    monkeypatch.setattr(api_credit, "_ntfy", lambda *a, **k: None)
    import fworker_sdk
    importlib.reload(fworker_sdk)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return types.SimpleNamespace(tmp=tmp_path, work=work, sdk=fworker_sdk, ac=api_credit)


def run_agent(env, script, settings=None, prompt="do it", extra=(), capsys=None):
    sdk = env.sdk
    sp = env.tmp / "settings.json"
    sp.write_text(json.dumps(settings or {"permissions": {
        "defaultMode": "acceptEdits",
        "allow": ["Read", "Grep", "Glob", "Edit", "Write", "Bash(echo *)", "Bash(cat *)",
                  "Bash(cd *)", "Bash(pwd)", "Bash(ls*)"],
        "deny": ["Bash(docker *)", "Read(**/.env)"]}}))
    args, _ = sdk.parse_args(["-p", prompt, "--model", "anthropic/claude-opus-5-5",
                              "--max-turns", "10", "--settings", str(sp),
                              "--permission-mode", "acceptEdits",
                              "--output-format", "stream-json", *extra])
    agent = sdk.Agent(args, json.loads(sp.read_text()))
    seq = list(script)
    seen = []

    def fake_call(session):
        seen.append(json.loads(json.dumps(session.messages)))
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    agent.call_model = fake_call
    rc = agent.run(args.prompt or None, args.resume)
    out = capsys.readouterr().out
    frames = [json.loads(l) for l in out.splitlines() if l.strip()]
    return rc, frames, seen, agent


def test_tool_loop_frames_and_ledger(env, capsys):
    rc, frames, seen, agent = run_agent(env, [
        msg([text("running"), tool("Bash", {"command": "echo hi"}, "t1")], "tool_use"),
        msg([text("EXIT_SIGNAL: true")]),
    ], capsys=capsys)
    assert rc == 0
    types_ = [f["type"] for f in frames]
    assert types_ == ["system", "assistant", "user", "assistant", "result"]
    assert frames[0]["subtype"] == "init" and frames[0]["session_id"].startswith("sdk-")
    tr = frames[2]["message"]["content"][0]
    assert tr["tool_use_id"] == "t1" and tr["content"] == "hi" and not tr.get("is_error")
    res = frames[-1]
    assert res["subtype"] == "success" and res["num_turns"] == 2
    assert res["result"] == "EXIT_SIGNAL: true"
    assert res["total_cost_usd"] == pytest.approx(2 * (1000 * 4 + 100 * 20) / 1e6)
    st = env.ac.status()
    assert st["calls"] == 2 and st["ledger_spend_usd"] == pytest.approx(res["total_cost_usd"], abs=1e-6)
    # the key never reaches a tool subprocess
    assert "FWORKER_SDK_API_KEY" not in agent.tool_env and "FWORKER_SDK_API_KEY" not in os.environ


def test_permission_deny_and_unlisted(env, capsys):
    rc, frames, _, agent = run_agent(env, [
        msg([tool("Bash", {"command": "docker ps"}, "a"),
             tool("Bash", {"command": "echo ok && rm -rf x"}, "b"),
             tool("Read", {"file_path": str(env.work / ".env")}, "c")], "tool_use"),
        msg([text("done")]),
    ], capsys=capsys)
    res = {b["tool_use_id"]: b for b in frames[2]["message"]["content"]}
    assert res["a"]["is_error"] and "Bash(docker *)" in res["a"]["content"]
    assert res["b"]["is_error"] and "rm -rf x" in res["b"]["content"]
    assert res["c"]["is_error"] and "Read(**/.env)" in res["c"]["content"]
    assert len(frames[-1]["permission_denials"]) == 3


def test_edit_requires_read_and_unique(env, capsys):
    f = env.work / "a.txt"
    f.write_text("x = 1\nx = 1\n")
    rc, frames, _, _ = run_agent(env, [
        msg([tool("Edit", {"file_path": str(f), "old_string": "x = 1", "new_string": "x = 2"}, "e1")], "tool_use"),
        msg([tool("Read", {"file_path": str(f)}, "r1"),
             tool("Edit", {"file_path": str(f), "old_string": "x = 1", "new_string": "x = 2"}, "e2"),
             tool("Edit", {"file_path": str(f), "old_string": "x = 1", "new_string": "x = 2",
                           "replace_all": True}, "e3")], "tool_use"),
        msg([text("done")]),
    ], capsys=capsys)
    first = frames[2]["message"]["content"][0]
    assert first["is_error"] and "not been read" in first["content"]
    second = {b["tool_use_id"]: b for b in frames[4]["message"]["content"]}
    assert "     1\tx = 1" in second["r1"]["content"]
    assert second["e2"]["is_error"] and "Found 2 matches" in second["e2"]["content"]
    assert not second["e3"].get("is_error")
    assert f.read_text() == "x = 2\nx = 2\n"


def test_write_outside_workdirs_denied(env, capsys):
    settings = {"permissions": {"defaultMode": "acceptEdits", "allow": ["Read"], "deny": []}}
    outside = env.tmp / "outside.txt"
    rc, frames, _, _ = run_agent(env, [
        msg([tool("Write", {"file_path": str(outside), "content": "x"}, "w1"),
             tool("Write", {"file_path": str(env.work / "in.txt"), "content": "y"}, "w2")], "tool_use"),
        msg([text("done")]),
    ], settings=settings, capsys=capsys)
    r = {b["tool_use_id"]: b for b in frames[2]["message"]["content"]}
    assert r["w1"]["is_error"] and not outside.exists()
    assert not r["w2"].get("is_error") and (env.work / "in.txt").read_text() == "y"


def _hook(tmp, name, body):
    p = tmp / name
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


def test_pretooluse_hook_blocks_and_rewrites(env, capsys):
    block = _hook(env.tmp, "block.sh",
                  'in=$(cat); case "$in" in *forbidden*) echo "guard says no" >&2; exit 2;; esac; exit 0\n')
    rewrite = _hook(env.tmp, "rw.sh",
                    'cat >/dev/null; echo \'{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
                    '"permissionDecision":"allow","updatedInput":{"command":"echo rewritten"}}}\'\n')
    settings = {"permissions": {"defaultMode": "acceptEdits", "allow": ["Bash(echo *)"]},
                "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
                    {"type": "command", "command": block},
                    {"type": "command", "command": rewrite}]}]}}
    rc, frames, _, _ = run_agent(env, [
        msg([tool("Bash", {"command": "echo forbidden"}, "h1"),
             tool("Bash", {"command": "echo original"}, "h2")], "tool_use"),
        msg([text("done")]),
    ], settings=settings, capsys=capsys)
    r = {b["tool_use_id"]: b for b in frames[2]["message"]["content"]}
    assert r["h1"]["is_error"] and "guard says no" in r["h1"]["content"]
    assert r["h2"]["content"] == "rewritten"


def test_stop_hook_blocks_once_then_session_ends(env, capsys):
    flag = env.tmp / "stopped-once"
    stop = _hook(env.tmp, "stop.sh",
                 f'in=$(cat); if [ ! -e {flag} ]; then touch {flag}; echo "write EXIT_SIGNAL" >&2; exit 2; fi; '
                 f'echo "$in" | grep -q \'"stop_hook_active": true\' || exit 9; exit 0\n')
    settings = {"permissions": {"allow": []}, "hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": stop}]}]}}
    rc, frames, seen, _ = run_agent(env, [msg([text("finished?")]), msg([text("EXIT_SIGNAL: true")])],
                                    settings=settings, capsys=capsys)
    assert rc == 0 and frames[-1]["result"] == "EXIT_SIGNAL: true"
    last_user = seen[1][-1]["content"][0]["text"]
    assert "write EXIT_SIGNAL" in last_user
    # Stop hook reads the transcript: it must hold the assistant turns in CC format
    sid = frames[0]["session_id"]
    tr = [json.loads(l) for l in open(os.path.join(os.environ["FWORKER_SDK_SESSIONS"], sid + ".jsonl"))]
    assert any(r["type"] == "assistant" and r["message"]["content"][0]["text"] == "finished?" for r in tr)


def test_credit_exhausted_stops_with_exit_3(env, capsys):
    import anthropic
    try:
        import httpx2 as httpx  # anthropic>=1.17 is built on its own httpx fork
    except ImportError:
        import httpx
    req = httpx.Request("POST", "http://x/v1/messages")
    resp = httpx.Response(400, request=req, json={"error": {"message":
        '{"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too low to access the Anthropic API."}}'}})
    err = anthropic.BadRequestError("Error code: 400 - credit balance is too low", response=resp,
                                    body=resp.json())
    rc, frames, _, _ = run_agent(env, [err], capsys=capsys)
    assert rc == env.sdk.EXIT_CREDIT
    assert frames[-1]["is_error"] and "API_CREDIT_EXHAUSTED" in frames[-1]["result"]
    assert not env.ac.status()["available"]


def test_resume_continues_session(env, capsys):
    rc, frames, _, _ = run_agent(env, [msg([text("first")])], capsys=capsys)
    sid = frames[0]["session_id"]
    rc, frames2, seen, _ = run_agent(env, [msg([text("second")])], prompt="more",
                                     extra=("--resume", sid), capsys=capsys)
    assert frames2[0]["session_id"] == sid
    roles = [m["role"] for m in seen[0]]
    assert roles == ["user", "assistant", "user"]
    assert seen[0][-1]["content"][0]["text"].startswith("more")


def test_bash_cwd_persists_and_output_capped(env, capsys):
    (env.work / "sub").mkdir()
    rc, frames, _, _ = run_agent(env, [
        msg([tool("Bash", {"command": "cd sub"}, "c1")], "tool_use"),
        msg([tool("Bash", {"command": "pwd"}, "c2"),
             tool("Bash", {"command": "echo " + "y" * 5000}, "c3")], "tool_use"),
        msg([text("done")]),
    ], capsys=capsys)
    r = {b["tool_use_id"]: b for b in frames[4]["message"]["content"]}
    assert r["c2"]["content"].endswith("/sub")
    assert "characters truncated" in r["c3"]["content"] and len(r["c3"]["content"]) < 2200


@pytest.mark.parametrize("cmd,segs", [
    ("git status", ["git status"]),
    ("cd /x && git diff | head -5", ["cd /x", "git diff", "head -5"]),
    ("echo 'a && b' ; ls", ["echo 'a && b'", "ls"]),
    ("FOO=1 python3 t.py", ["python3 t.py"]),
    ("echo $(whoami)", ["echo $(whoami)", "whoami"]),
    ('echo "x $(rm -rf /tmp/q)"', ['echo "x $(rm -rf /tmp/q)"', "rm -rf /tmp/q"]),
    ("sleep 1 & echo bg", ["sleep 1", "echo bg"]),
    ("ls 2>&1 | wc -l", ["ls 2>&1", "wc -l"]),
])
def test_split_bash(env, cmd, segs):
    assert env.sdk.split_bash(cmd) == segs


def test_path_rules(env):
    rx = env.sdk._path_glob_re
    assert rx("**/.env").match("/home/lumen/agent/.env")
    assert rx("/home/lumen/.secrets/**").match("/home/lumen/.secrets/foreman-apicredit.token")
    assert not rx("/home/lumen/.secrets/**").match("/home/lumen/secrets/x")
    assert rx("**/acceptance.json").match("/w/_runs/acceptance.json")
    assert env.sdk._bash_spec_re("git *").match("git commit -m 'x'")
    assert not env.sdk._bash_spec_re("git *").match("gitx")
    assert env.sdk._bash_spec_re("npm test:*").match("npm test --watch")


def test_resume_after_kill_mid_tool_closes_dangling_tool_use(env, capsys):
    """wq#5220 live failure: the watchdog killed the session during a long Bash call, the
    saved session ended in an unanswered tool_use, and the resume's API call was refused."""
    sdk = env.sdk
    s = sdk.Session("sdk-killed", "anthropic/claude-opus-5-5", str(env.work), "sys", [
        {"role": "user", "content": [{"type": "text", "text": "do it"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tu1", "name": "Bash",
                                           "input": {"command": "lumen-pr suite --wait 590"}}]}])
    s.save()
    rc, frames, seen, _ = run_agent(env, [msg([text("EXIT_SIGNAL: true")])], prompt="resume now",
                                    extra=("--resume", "sdk-killed"), capsys=capsys)
    assert rc == 0
    sent = seen[0]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]   # roles alternate
    last = sent[-1]["content"]
    assert last[0]["type"] == "tool_result" and last[0]["tool_use_id"] == "tu1" and last[0]["is_error"]
    assert "Interrupted" in last[0]["content"]
    assert last[1]["type"] == "text" and last[1]["text"].startswith("resume now")


def test_resume_after_tool_results_merges_prompt(env, capsys):
    sdk = env.sdk
    s = sdk.Session("sdk-mid", "anthropic/claude-opus-5-5", str(env.work), "sys", [
        {"role": "user", "content": [{"type": "text", "text": "do it"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t9", "name": "Bash", "input": {"command": "echo a"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t9", "content": "a"}]}])
    s.save()
    rc, frames, seen, _ = run_agent(env, [msg([text("ok")])], prompt="go on",
                                    extra=("--resume", "sdk-mid"), capsys=capsys)
    assert [m["role"] for m in seen[0]] == ["user", "assistant", "user"]
    assert [b["type"] for b in seen[0][-1]["content"]] == ["tool_result", "text"]


def test_long_bash_emits_keepalive_frames(env, capsys, monkeypatch):
    monkeypatch.setattr(env.sdk, "KEEPALIVE_S", 0.2)
    rc, frames, _, _ = run_agent(env, [
        msg([tool("Bash", {"command": "echo start; python3 -c 'import time; time.sleep(1)'; echo end"}, "k1")],
            "tool_use"),
        msg([text("done")]),
    ], settings={"permissions": {"allow": ["Bash(echo *)", "Bash(python3 *)"]}}, capsys=capsys)
    kinds = [(f["type"], (f.get("event") or {}).get("reason")) for f in frames]
    i_asst = kinds.index(("assistant", None))
    i_user = kinds.index(("user", None))
    ticks = [k for k in kinds[i_asst:i_user] if k == ("stream_event", "tool_running")]
    assert len(ticks) >= 2, kinds
    assert frames[i_user]["message"]["content"][0]["content"] == "start\nend"
