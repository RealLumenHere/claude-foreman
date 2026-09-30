"""wq#944 — the lint must fail on exactly the wq#909 shape it exists to catch."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import audit_prompt_lint as lint  # noqa: E402

LIVE_RENDER = lint.RENDER_BIN

GOOD_RULE = (
    'rules:\n  - "PRODUCT-LIVENESS: prove forward progress. '
    'See /home/lumen/instructions/playbooks/fleet-audit.md."\n'
)
GOOD_PROMPT = """# AUDIT

## SESSION BOOTSTRAP (mandatory, in order)
1. `bash /home/lumen/minerva/context/claude_context.sh --core`
2. Read `/home/lumen/instructions/playbooks/fleet-audit.md` — PRODUCT-LIVENESS is mandatory.

## Scope
whatever
"""


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def _render(tmp_path, body, name="render"):
    """A stand-in for instincts-render, called as `<bin> --file <path>`."""
    p = tmp_path / name
    p.write_text(f"#!/bin/sh\n{body}\n")
    p.chmod(0o755)
    return str(p)


@pytest.fixture(autouse=True)
def _full_render(tmp_path, monkeypatch):
    """Unit tests use a render that delivers the whole file (instincts-render's default,
    wq#958), not the live binary; test_live_fleet_state_is_wired uses the real one."""
    monkeypatch.setattr(lint, "RENDER_BIN", _render(tmp_path, 'cat "$2"'))


def test_passes_when_both_channels_wired(tmp_path):
    inst = _write(tmp_path, "instincts.yaml", GOOD_RULE)
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "FABLE_AUDIT_2026-08-23.md").write_text(GOOD_PROMPT)
    assert lint.main([inst, str(d)]) == 0


def test_rule_the_channel_does_not_deliver_fails(tmp_path):
    """Present-in-file is not present-in-session: a channel that cuts the file (the old
    `head -c 4096`) must fail the lint, and one that delivers it all must pass."""
    padded = 'rules:\n' + ('  - "filler rule padding the window."\n' * 200) + GOOD_RULE
    inst = _write(tmp_path, "instincts.yaml", padded)
    assert lint.check_instincts(inst, render=_render(tmp_path, 'head -c 4096 "$2"', "cut")) != []
    assert lint.check_instincts(inst) == []


def test_a_render_that_fails_or_is_missing_fails(tmp_path):
    inst = _write(tmp_path, "instincts.yaml", GOOD_RULE)
    assert lint.check_instincts(inst, render=_render(tmp_path, "exit 3", "fails")) != []
    assert lint.check_instincts(inst, render=str(tmp_path / "nope")) != []


def test_rule_without_playbook_pointer_fails(tmp_path):
    inst = _write(tmp_path, "instincts.yaml", 'rules:\n  - "PRODUCT-LIVENESS: vibes."\n')
    assert lint.check_instincts(inst) != []


def test_wq909_shape_fails_pointer_outside_the_bootstrap(tmp_path):
    """A playbook reference the prompt mentions but does not bootstrap does not count."""
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "FLEET_OPS_AUDIT_2026-08-23.md").write_text(
        "# AUDIT\n\n## SESSION BOOTSTRAP (mandatory, in order)\n"
        "1. `bash claude_context.sh --core`\n\n"
        "## Notes\nsee /home/lumen/instructions/playbooks/fleet-audit.md sometime\n"
    )
    problems = lint.check_prompts(str(d))
    assert len(problems) == 1 and "SESSION BOOTSTRAP" in problems[0]


def test_non_audit_and_backup_files_are_ignored(tmp_path):
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "FABLE_AUDIT_2026-08-23.md").write_text(GOOD_PROMPT)
    (d / "hathor-network-setup.md").write_text("## SESSION BOOTSTRAP\n1. do a thing\n")
    (d / "FABLE_AUDIT_2026-08-23.md.bak.20260823_wq944").write_text(
        "## SESSION BOOTSTRAP\n1. nothing\n"
    )
    assert lint.check_prompts(str(d)) == []


def test_empty_prompts_dir_fails_loudly(tmp_path):
    d = tmp_path / "prompts"
    d.mkdir()
    assert lint.check_prompts(str(d)) != []


def test_live_fleet_state_is_wired(monkeypatch):
    """The real files on this box, which is what wq#944 actually had to fix."""
    if not os.path.isdir(lint.PROMPTS_DIR) or not os.path.exists(lint.INSTINCTS):
        return
    monkeypatch.setattr(lint, "RENDER_BIN", LIVE_RENDER)
    assert lint.main([]) == 0
