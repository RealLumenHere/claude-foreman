import json

import gen_motd


def test_render_uses_every_ansi16_fallback_role_at_least_once():
    fallback = json.loads(gen_motd.DEFAULT_TOKENS.read_text())["ansi16_fallback"]
    out = gen_motd.render(fallback)
    assert str(gen_motd._ANSI16[fallback["primary"]]) in out
    assert str(gen_motd._ANSI16[fallback["secondary"]]) in out
    assert str(gen_motd._ANSI16[fallback["muted"]]) in out


def test_render_always_resets_before_end_of_each_line():
    fallback = {"primary": "cyan", "secondary": "yellow", "muted": "white"}
    out = gen_motd.render(fallback)
    for line in out.splitlines():
        assert line.endswith(gen_motd.RESET)


def test_default_tokens_file_is_valid_json_with_required_roles():
    data = json.loads(gen_motd.DEFAULT_TOKENS.read_text())
    for role in ("primary", "secondary", "muted"):
        assert role in data["ansi16_fallback"]
