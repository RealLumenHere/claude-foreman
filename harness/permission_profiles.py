"""Per-repo toolchain permission profiles, rendered at dispatch.

ISSUE-SOLUTIONS-fable.md §2b: worker-settings.json was one static allowlist for every
dispatched repo regardless of toolchain, so a python-shaped allowlist broke on a TS
repo and vice versa (ISSUE-LOG.md #14 — pipe targets + npm not allowed on the TS lane).
Instead each repo declares which toolchain profiles it needs
(permission-profiles/repos.d/<repo>.json); this module unions the named profile
fragments (permission-profiles/*.json) into one merged permissions pack at dispatch
time. An allowlist gap discovered in one node repo gets fixed in node-ts.json and
every node repo inherits it on its next dispatch — closed by profile, not by discovery.

A repo with no repos.d/<repo>.json entry is not yet migrated: resolve_settings_file()
returns the static settings file unchanged so dispatch behavior for it is untouched.
"""
import hashlib
import json
import os

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.join(HARNESS_DIR, "permission-profiles")
REPOS_DIR = os.path.join(PROFILES_DIR, "repos.d")


def repo_name(repo_path):
    name = os.path.basename(os.path.normpath(repo_path))
    if name.endswith(".git"):
        name = name[:-len(".git")]
    return name


def repo_config_path(repo_path):
    return os.path.join(REPOS_DIR, f"{repo_name(repo_path)}.json")


def _load_json(path):
    with open(path) as f:
        return json.load(f)


def _dedup_extend(dest, seen, rules):
    for rule in rules:
        if rule not in seen:
            seen.add(rule)
            dest.append(rule)


def render_permissions(repo_path, base_settings, archetype_allow=None):
    """Union the repo's declared toolchain profiles into base_settings' permissions
    block (hooks/defaultMode pass through unchanged). Returns None if the repo has no
    repos.d/<repo>.json AND no archetype_allow — caller falls back to base_settings
    unchanged. `archetype_allow` (wq#4762: verify-pass's repo-read grant, keyed by
    ARCHETYPE in fworker.resolve_worker_settings — distinct from repo_conf's own
    "extra_allow" key below, which is keyed by REPO) is unioned in regardless of
    whether the repo has a repos.d entry, so a verify-pass ticket on a repo with no
    profile declaration still gets it.
    """
    conf_path = repo_config_path(repo_path)
    if not os.path.isfile(conf_path):
        if not archetype_allow:
            return None
        merged = json.loads(json.dumps(base_settings))  # deep copy; hooks/defaultMode untouched
        merged.setdefault("permissions", {})
        allow = list(merged["permissions"].get("allow", []))
        seen = set(allow)
        _dedup_extend(allow, seen, archetype_allow)
        merged["permissions"]["allow"] = allow
        return merged
    repo_conf = _load_json(conf_path)
    profile_names = repo_conf.get("profiles") or ["base"]

    allow, deny = [], []
    seen_allow, seen_deny = set(), set()

    # base.json is a floor, not a toolchain-specific opt-in like the other profile
    # fragments are: always union its allow and deny regardless of whether "base" is in
    # the repo's declared profiles, so a repos.d/<repo>.json that forgets "base" (e.g.
    # {"profiles": ["python"]}) can't silently render an empty deny list and drop
    # protections guard.sh doesn't independently re-implement (wq#1267 CONTRADICT), and
    # can't silently lose base.json's allow rules the same way (wq#1382).
    base_prof = _load_json(os.path.join(PROFILES_DIR, "base.json"))
    _dedup_extend(allow, seen_allow, base_prof.get("allow", []))
    _dedup_extend(deny, seen_deny, base_prof.get("deny", []))

    for name in profile_names:
        prof_path = os.path.join(PROFILES_DIR, f"{name}.json")
        prof = _load_json(prof_path)
        _dedup_extend(allow, seen_allow, prof.get("allow", []))
        _dedup_extend(deny, seen_deny, prof.get("deny", []))

    _dedup_extend(allow, seen_allow, repo_conf.get("extra_allow", []))
    if archetype_allow:
        _dedup_extend(allow, seen_allow, archetype_allow)

    repo_abs = os.path.normpath(repo_path)
    verb_rules = [f"Bash({os.path.join(repo_abs, verb)})"
                  for verb in repo_conf.get("verbs", [])]
    _dedup_extend(allow, seen_allow, verb_rules)

    merged = json.loads(json.dumps(base_settings))  # deep copy; hooks/defaultMode untouched
    merged.setdefault("permissions", {})
    # CONTRACT (wq#1376): profiles are AUTHORITATIVE for a repo that declares them —
    # base_settings' allow is REPLACED, not unioned in. Unioning would re-import the flat
    # worker-settings.json allowlist that profiles exist to replace (a python-shaped rule
    # leaking onto a TS lane, ISSUE-LOG #14), making every repo's pack identical again.
    # Consequence: worker-settings.json's allow only reaches repos with NO repos.d/ entry.
    # Add a cross-toolchain allow rule to permission-profiles/base.json; a rule added only
    # to worker-settings.json is inert for pokemon/mailbot/foreman. test_permission_
    # profiles.py::test_worker_settings_allow_rules_all_exist_in_some_profile fails on a
    # rule that lands in the dead file, which is how this went unnoticed three times.
    merged["permissions"]["allow"] = allow
    merged["permissions"]["deny"] = deny
    return merged


# wq#5145 (wq#5041 gap 5, CodeAF's sec/review subharnesses): a review worker reads
# source and reports findings. It never builds, tests, runs an exploit, posts a comment
# or fixes anything; each of those is a separate request on a separate ticket. Like
# verify-probe this is a LANE grant, not a repo toolchain, so it replaces the per-repo
# repos.d/ union outright instead of being unioned into it: a repo's python/node
# fragments are Bash rules, and a review worker gets no Bash at all.
REVIEW_READONLY_ARCHETYPE = "review-readonly"
REVIEW_READONLY_ALLOW = ["Read", "Grep", "Glob"]
# Explicit denies on top of the empty-Bash allowlist. defaultMode "acceptEdits" (the
# worker-settings.json default) would otherwise auto-approve Edit/Write inside cwd,
# and a deny rule outranks both an allow rule and the mode.
REVIEW_READONLY_DENY = ["Bash", "Edit", "Write", "MultiEdit", "NotebookEdit",
                        "WebFetch", "WebSearch", "Task", "Agent"]  # subagent: both names (fworker._BG_BY_DEFAULT_TOOLS)


def render_review_readonly(base_settings):
    """The review-readonly permission pack: base_settings' hooks (guard.sh, stop gate)
    and defaultMode pass through unchanged; permissions.allow is REPLACED by
    REVIEW_READONLY_ALLOW and permissions.deny by base deny + REVIEW_READONLY_DENY. A
    fresh dict every call; base_settings is never mutated."""
    merged = json.loads(json.dumps(base_settings))
    perms = merged.setdefault("permissions", {})
    deny = []
    _dedup_extend(deny, set(), list(perms.get("deny", [])) + REVIEW_READONLY_DENY)
    perms["allow"] = list(REVIEW_READONLY_ALLOW)
    perms["deny"] = deny
    return merged


def is_review_readonly(settings_dict):
    """True when a rendered pack grants no tool outside Read/Grep/Glob and denies
    every write/exec tool in REVIEW_READONLY_DENY. The check fworker's tests and any
    consumer (mcp-guard, code audits) can run on a pack before trusting it."""
    perms = (settings_dict or {}).get("permissions") or {}
    allow = perms.get("allow") or []
    deny = set(perms.get("deny") or [])
    return (all(rule in REVIEW_READONLY_ALLOW for rule in allow)
            and all(rule in deny for rule in REVIEW_READONLY_DENY))


def resolve_review_readonly_settings_file(static_settings_file, out_dir):
    """Render the review-readonly pack under out_dir (content-addressed, same scheme as
    resolve_settings_file). Returns (settings_path, render_hash)."""
    merged = render_review_readonly(_load_json(static_settings_file))
    digest = render_hash(merged)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"settings-{REVIEW_READONLY_ARCHETYPE}-{digest[:12]}.json")
    if not os.path.isfile(out_path):
        tmp = out_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(merged, f, indent=2)
        os.replace(tmp, out_path)
    return out_path, digest


def render_hash(settings_dict):
    blob = json.dumps(settings_dict, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def resolve_settings_file(repo_path, static_settings_file, out_dir, archetype_allow=None):
    """Render this repo's merged permission pack under out_dir (fworker passes its
    per-ticket run_dir — one rendered file per (repo, profile-content) pair, content-
    addressed so a re-render with unchanged profiles is a no-op write).

    Returns (settings_path, render_hash). render_hash is None when the repo has no
    profile declaration (and no archetype_allow) and static_settings_file is returned
    unchanged — the existing, pre-2b behavior. `archetype_allow` (wq#4762) is folded
    into the same merged dict before hashing, so it is already covered by the
    content-addressed filename below — no separate cache key needed.
    """
    base_settings = _load_json(static_settings_file)
    merged = render_permissions(repo_path, base_settings, archetype_allow=archetype_allow)
    if merged is None:
        return static_settings_file, None

    digest = render_hash(merged)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"settings-{repo_name(repo_path)}-{digest[:12]}.json")
    if not os.path.isfile(out_path):
        tmp = out_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(merged, f, indent=2)
        os.replace(tmp, out_path)
    return out_path, digest
