"""ISSUE-SOLUTIONS-fable.md §2b — per-repo toolchain permission profiles, rendered at
dispatch. Zero quota (no `claude` child spawned).
"""
import json
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import permission_profiles  # noqa: E402

SETTINGS = os.path.join(HARNESS_DIR, "worker-settings.json")


def test_profile_fragments_parse_and_have_allow():
    for name in ("base", "python", "node-ts", "shell"):
        with open(os.path.join(permission_profiles.PROFILES_DIR, f"{name}.json")) as f:
            data = json.load(f)
        assert isinstance(data.get("allow"), list) and data["allow"]


def test_repo_name_strips_dotgit_and_takes_basename():
    assert permission_profiles.repo_name("/home/lumen/foreman") == "foreman"
    assert permission_profiles.repo_name("/home/lumen/pokemon/") == "pokemon"
    assert permission_profiles.repo_name("http://x/lumen/mailbot.git") == "mailbot"


def test_unconfigured_repo_falls_back_to_static_settings_unchanged(tmp_path):
    merged = permission_profiles.render_permissions(
        "/home/lumen/no-such-repo-configured", {"permissions": {"allow": [], "deny": []}})
    assert merged is None

    path, digest = permission_profiles.resolve_settings_file(
        "/home/lumen/no-such-repo-configured", SETTINGS, str(tmp_path))
    assert path == SETTINGS
    assert digest is None


def test_foreman_profile_gets_python_and_shell_not_node():
    with open(SETTINGS) as f:
        base = json.load(f)
    merged = permission_profiles.render_permissions("/home/lumen/foreman", base)
    assert merged is not None
    allow = merged["permissions"]["allow"]
    assert "Bash(pytest *)" in allow
    assert "Bash(bash scripts/*)" in allow
    assert "Bash(git *)" in allow  # base
    assert "Bash(npx *)" not in allow
    assert "Bash(npm run *)" not in allow


def test_pokemon_profile_gets_node_and_shell_not_python():
    with open(SETTINGS) as f:
        base = json.load(f)
    merged = permission_profiles.render_permissions("/home/lumen/pokemon", base)
    assert merged is not None
    allow = merged["permissions"]["allow"]
    assert "Bash(npm run *)" in allow
    assert "Bash(npx *)" in allow
    assert "Bash(bash scripts/*)" in allow
    assert "Bash(python3 *)" not in allow
    assert "Bash(pytest *)" not in allow


def test_mailbot_profile_gets_node_and_python():
    with open(SETTINGS) as f:
        base = json.load(f)
    merged = permission_profiles.render_permissions("/home/lumen/mailbot", base)
    assert merged is not None
    allow = merged["permissions"]["allow"]
    assert "Bash(npm run *)" in allow
    assert "Bash(python3 *)" in allow


def test_base_settings_only_allow_rule_is_deliberately_dropped():
    """wq#1376: pins the REPLACE contract. A rule that exists only in the static
    worker-settings.json does NOT survive rendering for a profile-declared repo — that is
    intended (unioning would re-import the flat allowlist profiles exist to replace), and
    it is why an allow rule must be added to permission-profiles/base.json instead.
    """
    base = {"permissions": {"allow": ["Bash(only-in-static-settings)"], "deny": []}}
    merged = permission_profiles.render_permissions("/home/lumen/pokemon", base)
    assert "Bash(only-in-static-settings)" not in merged["permissions"]["allow"]
    assert "Bash(git *)" in merged["permissions"]["allow"]  # from base.json profile


def test_worker_settings_allow_rules_all_exist_in_some_profile():
    """wq#1376 regression guard: three fixes in a row (wq#1323, wq#1347) landed an allow
    rule in worker-settings.json, which is inert for every repos.d-declared repo. Any rule
    worth having in the static pack must also exist in a profile fragment, or the next
    dispatched pokemon/mailbot/foreman worker still gets 'requires approval'.
    """
    with open(SETTINGS) as f:
        static_allow = json.load(f)["permissions"]["allow"]
    union = set()
    for name in ("base", "python", "node-ts", "shell"):
        with open(os.path.join(permission_profiles.PROFILES_DIR, f"{name}.json")) as f:
            union.update(json.load(f).get("allow", []))
    orphans = [r for r in static_allow if r not in union]
    assert not orphans, (
        "allow rules present only in worker-settings.json are inert for profile-declared "
        f"repos; add them to permission-profiles/base.json: {orphans}")


def test_scoped_messenger_restart_reaches_every_declared_repo():
    """wq#1376 / blocks wq#1345 clause 3. Exactly scoped — not 'systemctl restart*'."""
    with open(SETTINGS) as f:
        base = json.load(f)
    rule = "Bash(systemctl restart lumen-messenger)"
    for repo in ("/home/lumen/pokemon", "/home/lumen/mailbot", "/home/lumen/foreman"):
        allow = permission_profiles.render_permissions(repo, base)["permissions"]["allow"]
        assert rule in allow, repo
        assert "Bash(systemctl restart*)" not in allow, repo
        assert "Bash(systemctl restart *)" not in allow, repo


def test_merged_pack_keeps_deny_list_and_hooks_from_static_settings():
    with open(SETTINGS) as f:
        base = json.load(f)
    merged = permission_profiles.render_permissions("/home/lumen/foreman", base)
    assert "Bash(*reboot*)" in merged["permissions"]["deny"]
    assert any(".env" in d for d in merged["permissions"]["deny"])
    assert merged["hooks"] == base["hooks"]
    assert merged["permissions"]["defaultMode"] == base["permissions"]["defaultMode"]


def test_verbs_render_repo_scoped_bash_rule():
    conf = {"profiles": ["base"], "extra_allow": [], "verbs": ["bin/rt-build*", "bin/rt-test*"]}
    repos_dir = permission_profiles.REPOS_DIR
    fixture_path = os.path.join(repos_dir, "wq1258-fixture-repo.json")
    with open(fixture_path, "w") as f:
        json.dump(conf, f)
    try:
        with open(SETTINGS) as f:
            base = json.load(f)
        merged = permission_profiles.render_permissions(
            "/home/lumen/wq1258-fixture-repo", base)
        allow = merged["permissions"]["allow"]
        assert "Bash(/home/lumen/wq1258-fixture-repo/bin/rt-build*)" in allow
        assert "Bash(/home/lumen/wq1258-fixture-repo/bin/rt-test*)" in allow
    finally:
        os.remove(fixture_path)


def test_extra_allow_included_and_deduped_against_profile_rules():
    conf = {"profiles": ["python"], "extra_allow": ["Bash(pytest *)", "Bash(custom-tool *)"]}
    fixture_path = os.path.join(permission_profiles.REPOS_DIR, "wq1258-fixture-repo.json")
    with open(fixture_path, "w") as f:
        json.dump(conf, f)
    try:
        with open(SETTINGS) as f:
            base = json.load(f)
        merged = permission_profiles.render_permissions(
            "/home/lumen/wq1258-fixture-repo", base)
        allow = merged["permissions"]["allow"]
        assert allow.count("Bash(pytest *)") == 1  # deduped, not doubled
        assert "Bash(custom-tool *)" in allow
    finally:
        os.remove(fixture_path)


def test_deny_floor_holds_even_when_repo_config_omits_base_profile():
    """wq#1267 CONTRADICT: {"profiles": ["python"]} (no "base") used to render an empty
    deny list, dropping Bash(env)/printenv/.env-read protections guard.sh does not
    independently re-implement. base.json's deny is now an unconditional floor.
    """
    conf = {"profiles": ["python"]}
    fixture_path = os.path.join(permission_profiles.REPOS_DIR, "wq1258-fixture-repo.json")
    with open(fixture_path, "w") as f:
        json.dump(conf, f)
    try:
        with open(SETTINGS) as f:
            base = json.load(f)
        merged = permission_profiles.render_permissions(
            "/home/lumen/wq1258-fixture-repo", base)
        deny = merged["permissions"]["deny"]
        assert "Bash(*reboot*)" in deny
        assert any("env" in d for d in deny)
        assert any(".env" in d for d in deny)
    finally:
        os.remove(fixture_path)


def test_render_hash_deterministic_and_content_addressed():
    with open(SETTINGS) as f:
        base = json.load(f)
    a = permission_profiles.render_permissions("/home/lumen/foreman", base)
    b = permission_profiles.render_permissions("/home/lumen/foreman", base)
    assert permission_profiles.render_hash(a) == permission_profiles.render_hash(b)


def test_resolve_settings_file_writes_once_and_is_idempotent(tmp_path):
    path1, digest1 = permission_profiles.resolve_settings_file(
        "/home/lumen/foreman", SETTINGS, str(tmp_path))
    mtime1 = os.path.getmtime(path1)
    path2, digest2 = permission_profiles.resolve_settings_file(
        "/home/lumen/foreman", SETTINGS, str(tmp_path))
    assert path1 == path2
    assert digest1 == digest2
    assert os.path.getmtime(path2) == mtime1  # no rewrite on the second render

    with open(path1) as f:
        rendered = json.load(f)
    assert "Bash(pytest *)" in rendered["permissions"]["allow"]


def test_fworker_resolve_worker_settings_falls_back_for_unconfigured_repo(tmp_path):
    import importlib
    import fworker
    importlib.reload(fworker)
    fworker.RUNS_ROOT = str(tmp_path)
    settings_file = fworker.resolve_worker_settings(
        "/home/lumen/no-such-repo-configured", "wq1258-test")
    assert os.path.realpath(settings_file) == os.path.realpath(SETTINGS)


def test_fworker_resolve_worker_settings_renders_for_foreman(tmp_path):
    import importlib
    import fworker
    importlib.reload(fworker)
    fworker.RUNS_ROOT = str(tmp_path)
    settings_file = fworker.resolve_worker_settings("/home/lumen/foreman", "wq1258-test")
    assert settings_file != fworker.SETTINGS_FILE
    with open(settings_file) as f:
        rendered = json.load(f)
    assert "Bash(pytest *)" in rendered["permissions"]["allow"]
    assert "Bash(npx *)" not in rendered["permissions"]["allow"]
