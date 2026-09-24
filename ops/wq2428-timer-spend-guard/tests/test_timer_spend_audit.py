"""wq#2428 — timer_spend_audit classification and systemctl parsing.

Every case here is a shape the live fleet actually produced on 2026-09-24; the
false-positive tests in particular are the exact lines that made the tool's first
run report three cloud findings that were prose, not calls.
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import timer_spend_audit as tsa  # noqa: E402


def unit(**kw):
    base = {"id": "x.service", "argv": [], "environment": [],
            "environment_files": [], "scripts": {}}
    base.update(kw)
    return base


def scanned(*markers, models=()):
    return {"markers": list(markers), "models": list(models), "status": "scanned"}


# ── systemctl output parsing ────────────────────────────────────────────────────

SHOW = """Id=lumen-scout.service
ActiveState=inactive
UnitFileState=static
ExecStart={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 /home/lumen/autonomy/lumen_scout_graph.py ; ignore_errors=no ; start_time=[n/a] ; pid=0 }
Environment=

Id=sia-news-pull.service
ExecStart={ path=/home/lumen/bin/sia-news-pull ; argv[]=/home/lumen/bin/sia-news-pull ; ignore_errors=no }
ExecStartPost={ path=/home/lumen/bin/lumen-cron-stamp ; argv[]=/home/lumen/bin/lumen-cron-stamp sia-news-pull.service ; ignore_errors=yes }
"""


def test_parse_show_blocks_splits_on_blank_lines():
    blocks = tsa.parse_show_blocks(SHOW)
    assert [b["Id"][0] for b in blocks] == ["lumen-scout.service", "sia-news-pull.service"]
    assert blocks[0]["Environment"] == [""]


def test_exec_argv_reads_every_exec_directive():
    blocks = tsa.parse_show_blocks(SHOW)
    assert tsa.exec_argv(blocks[0]) == [["/usr/bin/python3",
                                         "/home/lumen/autonomy/lumen_scout_graph.py"]]
    assert tsa.exec_argv(blocks[1]) == [
        ["/home/lumen/bin/sia-news-pull"],
        ["/home/lumen/bin/lumen-cron-stamp", "sia-news-pull.service"],
    ]


def test_env_file_path_drops_the_ignore_errors_suffix():
    assert tsa.env_file_path("/home/lumen/agent/.env (ignore_errors=no)") == \
        "/home/lumen/agent/.env"


def test_script_target_walks_past_the_interpreter():
    assert tsa.script_target(["/usr/bin/python3", "/home/lumen/x.py"]) == "/home/lumen/x.py"
    assert tsa.script_target(["/home/lumen/bin/sia-news-pull"]) == "/home/lumen/bin/sia-news-pull"
    assert tsa.script_target(["/usr/bin/python3", "-u", "/home/lumen/x.py"]) == "/home/lumen/x.py"
    assert tsa.script_target(["/usr/bin/python3"]) is None


# ── markers ─────────────────────────────────────────────────────────────────────

def test_argv_claude_basename_is_structural_not_regex():
    assert tsa.markers_in_exec(["/usr/local/bin/claude", "-p", "hi"]) == ["claude-cli"]
    assert tsa.markers_in_exec(["/home/lumen/bin/claude-shim"]) == []


def test_model_id_in_argv_counts_as_a_selection():
    assert "claude-model-id" in tsa.markers_in_exec(["wq", "--model", "claude-opus-5"])


@pytest.mark.parametrize("line", [
    'timeout 3600 claude -p "$PROMPT" \\',
    'subprocess.run(["claude", "-p", prompt])',
    "from anthropic import Anthropic",
    "client = anthropic.Anthropic()",
    'r = requests.post("https://api.anthropic.com/v1/messages")',
])
def test_script_invocation_shapes_are_found(line):
    assert tsa.markers_in_script(line)


@pytest.mark.parametrize("line", [
    "    # 2) re-stamp all repos (fix stale/failed stamps) — needs claude on PATH",
    "    `claude --model opus-5`, which 404s (model_not_found) since the CLI only knows",
    '           f"{json_quote(msg)} --owner claude", timeout=20)',
    'f"(wq#1442) — fworker would hand this straight to `claude --model` "',
])
def test_prose_about_claude_is_not_a_call(line):
    """These four lines are why the first live run reported three phantom findings."""
    assert tsa.markers_in_script(line) == []


@pytest.mark.parametrize("body", [
    # signal_engine.py:47 and worklog_gen.py:17 — single-line keyword collections.
    'EXCLUDE=("persona","brain","opus","claude","fable","anthropic","litellm")',
    'KEYWORDS = ("ai","llm","ollama","mcp","claude","gpt","openai","anthropic")',
    # minerva_factory.py:284 — the fleet's own deny-list of Anthropic env vars.
    "FORBIDDEN_KEY_ENVS = ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY')",
    # lumen_intel.py:88-101 — the same shape spread over several lines.
    'HIGH_VALUE = {\n    "swe-bench", "claude", "gpt", "llama",\n    "deepseek",\n}',
])
def test_a_keyword_inside_a_constant_collection_is_not_a_call(body):
    """All four CLOUD findings of the 2026-09-24 sweep were this shape (wq#2456)."""
    assert tsa.markers_in_script(body) == []


def test_a_constant_collection_does_not_swallow_the_rest_of_the_file():
    """Depth tracking has to close the literal, or every later call goes unreported."""
    body = 'EXCLUDE = ("claude", "gpt")\nsubprocess.run(["claude", "-p", prompt])'
    assert tsa.markers_in_script(body) == [(2, "claude-cli")]


def test_a_bracket_inside_a_quoted_keyword_does_not_open_a_block():
    body = 'PATTERNS = ("a[",)\nsubprocess.run(["claude", "-p", prompt])'
    assert tsa.markers_in_script(body) == [(2, "claude-cli")]


@pytest.mark.parametrize("body", [
    'ENDPOINTS = ("https://api.anthropic.com/v1/messages",)',
    'CMDS = ["claude -p summarise"]',
])
def test_live_config_inside_a_constant_still_counts(body):
    """Only membership shapes are suppressed — a URL or a real argv is still spend."""
    assert tsa.markers_in_script(body)


def test_a_key_lookup_outside_a_collection_is_still_a_finding():
    assert tsa.markers_in_script('key = os.environ["ANTHROPIC_API_KEY"]')


@pytest.mark.parametrize("line", [
    'rc, out, err = _run_under_unit_env(path, ["claude", "auth", "status"])',
    "claude --version",
    "claude mcp list",
])
def test_non_billing_subcommands_are_not_spend(line):
    """dispatch_recover.py's auth probe is a real CLI call that cannot cost a token."""
    assert tsa.markers_in_script(line) == []


def test_non_billing_applies_to_execstart_argv_too():
    assert tsa.markers_in_exec(["/usr/local/bin/claude", "auth", "status"]) == []
    assert tsa.markers_in_exec(["/usr/local/bin/claude", "-p", "go"]) == ["claude-cli"]


def test_script_markers_carry_line_numbers():
    body = "import os\nimport anthropic\nprint(1)\n"
    assert tsa.markers_in_script(body) == [(2, "anthropic-sdk")]


# ── gateway pin ─────────────────────────────────────────────────────────────────

def test_gateway_pin_reads_the_base_url():
    assert tsa.gateway_pin(["ANTHROPIC_BASE_URL=http://localhost:4000"]) == "local"
    assert tsa.gateway_pin(["ANTHROPIC_BASE_URL=https://api.anthropic.com"]) == "cloud"
    assert tsa.gateway_pin([""]) is None


def test_gateway_pin_handles_several_assignments_on_one_line():
    env = ["FOO=1 ANTHROPIC_BASE_URL=http://localhost:4000 BAR=2"]
    assert tsa.gateway_pin(env) == "local"


# ── classification ──────────────────────────────────────────────────────────────

def test_clean_when_everything_was_read_and_nothing_reaches_anthropic():
    u = unit(argv=[["/home/lumen/bin/sia-news-pull"]],
             scripts={"/home/lumen/bin/sia-news-pull": scanned()})
    assert tsa.classify(u)[0] == "clean"


def test_cloud_when_a_scanned_script_invokes_the_cli():
    u = unit(argv=[["/home/lumen/bin/gh-lane-worker"]],
             scripts={"/home/lumen/bin/gh-lane-worker": scanned((64, "claude-cli"))})
    verdict, reasons = tsa.classify(u)
    assert verdict == "cloud"
    assert "/home/lumen/bin/gh-lane-worker:64 claude-cli" in reasons


def test_pinned_when_the_unit_points_the_sdk_at_the_gateway():
    u = unit(argv=[["/usr/bin/python3", "/home/lumen/x.py"]],
             environment=["ANTHROPIC_BASE_URL=http://localhost:4000"],
             scripts={"/home/lumen/x.py": scanned((3, "anthropic-sdk"))})
    assert tsa.classify(u)[0] == "pinned"


def test_a_base_url_still_on_anthropic_com_is_not_a_pin():
    u = unit(argv=[["/usr/bin/python3", "/home/lumen/x.py"]],
             environment=["ANTHROPIC_BASE_URL=https://api.anthropic.com"],
             scripts={"/home/lumen/x.py": scanned((3, "anthropic-sdk"))})
    assert tsa.classify(u)[0] == "cloud"


def test_unreadable_script_is_unknown_never_clean():
    u = unit(argv=[["/usr/bin/python3", "/home/lumen/autonomy/lumen_scout_graph.py"]],
             scripts={"/home/lumen/autonomy/lumen_scout_graph.py":
                      {"markers": [], "status": "out-of-roots"}})
    verdict, reasons = tsa.classify(u)
    assert verdict == "unknown"
    assert "out-of-roots" in reasons[-1]


def test_environment_file_makes_the_env_unknowable():
    u = unit(argv=[["/home/lumen/bin/sia-news-pull"]],
             environment_files=["/home/lumen/agent/.env"],
             scripts={"/home/lumen/bin/sia-news-pull": scanned()})
    assert tsa.classify(u)[0] == "unknown"


def test_a_known_call_outranks_an_unread_sibling():
    """Unknown never masks a confirmed finding — cloud is the louder verdict."""
    u = unit(argv=[["/home/lumen/bin/a"], ["/opt/b"]],
             scripts={"/home/lumen/bin/a": scanned((1, "claude-cli")),
                      "/opt/b": {"markers": [], "status": "out-of-roots"}})
    assert tsa.classify(u)[0] == "cloud"


# ── scanning honours --roots ────────────────────────────────────────────────────

def test_scan_script_refuses_paths_outside_roots(tmp_path):
    p = tmp_path / "s.sh"
    p.write_text("claude -p hi\n")
    out = tsa.scan_script(str(p), roots=["/nowhere"])
    assert out["status"] == "out-of-roots" and out["markers"] == []
    out = tsa.scan_script(str(p), roots=[str(tmp_path)])
    assert out["status"] == "scanned" and out["markers"] == [(1, "claude-cli")]


def test_scan_script_reports_a_missing_file_as_unreadable(tmp_path):
    assert tsa.scan_script(str(tmp_path / "nope"))["status"] == "unreadable"


# ── model selection (wq#2456) ───────────────────────────────────────────────────

def test_models_in_script_reads_the_cli_flag_and_a_kwarg():
    assert tsa.models_in_script("run(['claude','-p',q,'--model','opus-5'])") == ["opus-5"]
    assert tsa.models_in_script('resp = client.create(model="claude-opus-5")') \
        == ["claude-opus-5"]
    assert tsa.models_in_script("cmd = f'claude --model={M} -p'") == []


def test_models_in_script_ignores_prose():
    assert tsa.models_in_script("# claude --model claude-opus-5 is the idiom") == []
    assert tsa.models_in_script('"""use `claude --model claude-opus-5` here."""') == []


def test_a_cloud_finding_names_the_model_it_selects():
    u = unit(scripts={"/s.py": scanned((3, "claude-cli"), models=["claude-opus-5"])})
    verdict, reasons = tsa.classify(u)
    assert verdict == "cloud"
    assert "/s.py selects model: claude-opus-5" in reasons


def test_install_script_parses():
    """`bash -n` is off the worker allowlist for paths under ops/ (ISSUE-LOG #645), so
    the only way a worker can lint the shell it is about to land is from inside a test."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = subprocess.run(["bash", "-n", os.path.join(here, "install.sh")],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


def test_a_model_id_alone_is_not_a_finding():
    """models_in_script never sets a verdict: a bare id with no invocation shape is
    still `clean`, or the audit would flag every models.yaml-shaped config."""
    u = unit(scripts={"/s.py": scanned(models=["claude-opus-5"])})
    verdict, reasons = tsa.classify(u)
    assert verdict == "clean" and reasons == []


# ── report / exit code ──────────────────────────────────────────────────────────

def test_report_orders_cloud_first_and_hides_clean_by_default():
    findings = [{"unit": "b.service", "timer": "b.timer", "timer_state": "enabled",
                 "verdict": "clean", "reasons": []},
                {"unit": "a.service", "timer": "a.timer", "timer_state": "enabled",
                 "verdict": "cloud", "reasons": ["x:1 claude-cli"]}]
    findings.sort(key=lambda f: (tsa.ORDER.index(f["verdict"]), f["unit"]))
    head, lines = tsa.report(findings)
    assert "cloud=1" in head and "clean=1" in head
    assert lines[0].startswith("  CLOUD    a.service")
    assert not any("b.service" in ln for ln in lines)
