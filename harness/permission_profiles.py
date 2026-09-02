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


def render_permissions(repo_path, base_settings):
    """Union the repo's declared toolchain profiles into base_settings' permissions
    block (hooks/defaultMode pass through unchanged). Returns None if the repo has no
    repos.d/<repo>.json — caller falls back to base_settings unchanged.
    """
    conf_path = repo_config_path(repo_path)
    if not os.path.isfile(conf_path):
        return None
    repo_conf = _load_json(conf_path)
    profile_names = repo_conf.get("profiles") or ["base"]

    allow, deny = [], []
    seen_allow, seen_deny = set(), set()

    # Deny rules are a safety floor, not a toolchain-specific opt-in like allow
    # fragments are: always union base.json's deny regardless of whether "base" is in
    # the repo's declared profiles, so a repos.d/<repo>.json that forgets "base" (e.g.
    # {"profiles": ["python"]}) can't silently render an empty deny list and drop
    # protections guard.sh doesn't independently re-implement (wq#1267 CONTRADICT).
    base_prof = _load_json(os.path.join(PROFILES_DIR, "base.json"))
    _dedup_extend(deny, seen_deny, base_prof.get("deny", []))

    for name in profile_names:
        prof_path = os.path.join(PROFILES_DIR, f"{name}.json")
        prof = _load_json(prof_path)
        _dedup_extend(allow, seen_allow, prof.get("allow", []))
        _dedup_extend(deny, seen_deny, prof.get("deny", []))

    _dedup_extend(allow, seen_allow, repo_conf.get("extra_allow", []))

    repo_abs = os.path.normpath(repo_path)
    verb_rules = [f"Bash({os.path.join(repo_abs, verb)})"
                  for verb in repo_conf.get("verbs", [])]
    _dedup_extend(allow, seen_allow, verb_rules)

    merged = json.loads(json.dumps(base_settings))  # deep copy; hooks/defaultMode untouched
    merged.setdefault("permissions", {})
    merged["permissions"]["allow"] = allow
    merged["permissions"]["deny"] = deny
    return merged


def render_hash(settings_dict):
    blob = json.dumps(settings_dict, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def resolve_settings_file(repo_path, static_settings_file, out_dir):
    """Render this repo's merged permission pack under out_dir (fworker passes its
    per-ticket run_dir — one rendered file per (repo, profile-content) pair, content-
    addressed so a re-render with unchanged profiles is a no-op write).

    Returns (settings_path, render_hash). render_hash is None when the repo has no
    profile declaration and static_settings_file is returned unchanged — the existing,
    pre-2b behavior.
    """
    base_settings = _load_json(static_settings_file)
    merged = render_permissions(repo_path, base_settings)
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
