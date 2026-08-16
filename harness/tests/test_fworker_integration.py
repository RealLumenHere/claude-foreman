"""fworker end-to-end integration tests, zero Anthropic quota.

Exercises the full lifecycle plumbing (claim -> worktree -> spawn -> stream-parse ->
classify -> wq lifecycle) against a scratch git repo + scratch wq DB + a fake `claude`
shim (tests/fake_claude.py) that emits canned stream-json, per design
T2A_WORKER_HARNESS_DESIGN.md §12 item 5 — substituting the shim for a real sonnet-5@low
run so this build's probe gate costs no live quota. A real quota-costing smoke run
(the design's literal item 5/6) is a follow-up the orchestrator can schedule off-peak.

Covers the ticket's two named adversarial criteria end-to-end (not just at the unit
level): a denied verb is blocked at the hook during a real spawn, and a synthetic
rejected-limit frame drives fworker's real LIMIT path (release + exit code), not CRASH.
"""
import json
import os
import subprocess
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402 — reuse slugify() so pre-created branch names match make_worktree's

FWORKER = os.path.join(HARNESS_DIR, "fworker.py")
FAKE_CLAUDE = os.path.join(TESTS_DIR, "fake_claude.py")
FAKE_GOVERNOR = os.path.join(TESTS_DIR, "fake_governor.py")
WQ_BIN = "/home/lumen/bin/wq"


def make_scratch_repo(tmp_path):
    repo = tmp_path / "scratch_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@lumen"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "README.md").write_text("scratch\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return str(repo)


def make_scratch_env(tmp_path, ticket, scenario, extra=None):
    wq_db = str(tmp_path / "scratch_wq.db")
    events_dir = str(tmp_path / "events")
    runs_root = str(tmp_path / "_runs")
    worktrees_root = str(tmp_path / "worktrees")
    os.makedirs(events_dir, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "WQ_DB": wq_db,
        "WQ_AGENT": "test-fworker-agent",
        "FOREMAN_EVENTS_DIR": events_dir,
        "FOREMAN_RUNS_ROOT": runs_root,
        "FOREMAN_WORKTREES_ROOT": worktrees_root,
        "FOREMAN_STOP_FILE": str(tmp_path / "STOP_nonexistent"),
        "FOREMAN_GOVERNOR_CLI": FAKE_GOVERNOR,
        "FOREMAN_WQ_BIN": WQ_BIN,
        "FOREMAN_LUMEN_PR_BIN": "/bin/false",  # unreachable on purpose; SUCCESS path shouldn't need it
        "FOREMAN_NTFY_BIN": "/bin/true",
        "FAKE_CLAUDE_SCENARIO": scenario,
        "FAKE_CLAUDE_TICKET": str(ticket),
        "FAKE_CLAUDE_GUARD_SH": os.path.join(HARNESS_DIR, "guard.sh"),
        "FAKE_CLAUDE_WQ_BIN": WQ_BIN,
    })
    if extra:
        env.update(extra)
    return env, wq_db, events_dir, runs_root, worktrees_root


def wq_add(env, title):
    p = subprocess.run([WQ_BIN, "add", "--title", title], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    # "added task 1 [auto]"
    return int(p.stdout.strip().split()[2])


def wq_status(env, ticket_id):
    p = subprocess.run([WQ_BIN, "show", str(ticket_id)], capture_output=True, text=True, env=env)
    for line in p.stdout.splitlines():
        if line.strip().startswith("status"):
            return line.split(":", 1)[1].strip()
    return None


def run_fworker(env, ticket_id, repo, scenario, model="claude-sonnet-5", effort="high",
                 archetype="mechanical-edit", extra_args=None, timeout=60):
    cmd = [sys.executable, FWORKER, "run", "--ticket", str(ticket_id), "--repo", repo,
           "--model", model, "--effort", effort, "--archetype", archetype,
           "--claude-bin", FAKE_CLAUDE,
           "--worker-id", f"test-fworker-wq{ticket_id}"] + (extra_args or [])
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)


def test_success_end_to_end_closes_ticket_and_isolates_worktree(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(tmp_path, 0, "success")
    ticket_id = wq_add(env, "scratch mechanical-edit ticket")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "success")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_status(env, ticket_id) == "done"

    # worktree isolation: a dedicated dir was created, parent repo status is untouched.
    wt = os.path.join(worktrees_root, f"wq{ticket_id}-a1")
    assert os.path.isdir(wt)
    parent_status = subprocess.run(["git", "-C", repo, "status", "--porcelain"],
                                    capture_output=True, text=True)
    assert parent_status.stdout.strip() == ""

    # effort verification channel: guard.sh's lifecycle events carry $CLAUDE_EFFORT-
    # equivalent (effort.level from the hook envelope) matching what was dispatched.
    events_file = os.path.join(events_dir, f"test-fworker-wq{ticket_id}.jsonl")
    assert os.path.exists(events_file)
    lines = [json.loads(l) for l in open(events_file) if l.strip()]
    assert any(l["event"] == "start" for l in lines)
    assert all(l["effort"] == "high" for l in lines if l["event"] in ("start", "stop", "end"))


def test_denied_verb_blocked_at_hook_during_real_spawn(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(
        tmp_path, 0, "denied_verb_blocked")
    ticket_id = wq_add(env, "scratch denied-verb ticket")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "denied_verb_blocked")
    # both attempts CRASH (no rate-limit signal) -> blocked after MAX_ATTEMPTS
    assert wq_status(env, ticket_id) == "blocked"

    events_file = os.path.join(events_dir, f"test-fworker-wq{ticket_id}.jsonl")
    lines = [json.loads(l) for l in open(events_file) if l.strip()]
    blocked = [l for l in lines if l.get("event") == "blocked"]
    assert blocked, "guard.sh should have logged a blocked event for --remove-orphans"
    assert "remove-orphans" in blocked[0]["reason"]


def test_rejected_limit_frame_drives_limit_path_not_crash(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(
        tmp_path, 0, "rejected_limit")
    ticket_id = wq_add(env, "scratch rate-limit ticket")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "rejected_limit")
    # LIMIT with no boundable resets_at within window -> fworker releases the ticket
    # and exits EXIT_LIMIT (13), NOT the blocked/crash path.
    assert result.returncode == 13, result.stdout + result.stderr
    status = wq_status(env, ticket_id)
    assert status == "open", f"expected ticket released back to open, got {status}"

    attempt_files = os.listdir(os.path.join(runs_root, f"wq{ticket_id}"))
    assert any(f.startswith("attempt-") for f in attempt_files)
    with open(os.path.join(runs_root, f"wq{ticket_id}", "attempt-1.json")) as f:
        record = json.load(f)
    assert record["classification"] == "LIMIT"
    assert record["classification"] != "CRASH"


def test_thinking_frames_dont_double_count_toward_turn_ceiling(tmp_path):
    # wq#645 regression: 10 real turns shaped as (thinking-only frame, tool_use frame)
    # pairs, same as the live 2.1.233 CLI. mechanical-edit's max_turns=15 means the
    # pre-fix double-counting bug SIGTERMs this at raw frame 15 (~7.5 real turns) and
    # misclassifies it TURNS; the fix must let it run all 10 real turns and succeed.
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(
        tmp_path, 0, "thinking_paired_turns")
    ticket_id = wq_add(env, "scratch thinking-paired ticket")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "thinking_paired_turns",
                          archetype="mechanical-edit")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_status(env, ticket_id) == "done"

    with open(os.path.join(runs_root, f"wq{ticket_id}", "attempt-1.json")) as f:
        record = json.load(f)
    assert record["classification"] == "SUCCESS"
    assert record["turns"] == 10


def test_dep_preflight_defers_without_claiming_or_spawning(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(tmp_path, 0, "success")
    dep_id = wq_add(env, "scratch dep ticket, left open on purpose")
    p = subprocess.run([WQ_BIN, "add", "--title", "scratch dependent ticket",
                         "--deps", str(dep_id)], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    ticket_id = int(p.stdout.strip().split()[2])
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "success")
    assert result.returncode == 14, result.stdout + result.stderr
    assert "DEFERRED" in result.stdout

    # never claimed (still open, no claimed_by) and no worktree/run-state was touched.
    assert wq_status(env, ticket_id) == "open"
    assert not os.path.isdir(os.path.join(worktrees_root, f"wq{ticket_id}-a1"))
    assert not os.path.isdir(os.path.join(runs_root, f"wq{ticket_id}"))


def test_dep_preflight_proceeds_once_dep_is_done(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(tmp_path, 0, "success")
    dep_id = wq_add(env, "scratch dep ticket, closed immediately")
    subprocess.run([WQ_BIN, "claim", "--id", str(dep_id)], capture_output=True, text=True, env=env)
    subprocess.run([WQ_BIN, "done", str(dep_id), "--notes", "n/a"],
                    capture_output=True, text=True, env=env)
    p = subprocess.run([WQ_BIN, "add", "--title", "scratch dependent ticket, ready",
                         "--deps", str(dep_id)], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    ticket_id = int(p.stdout.strip().split()[2])
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "success")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_status(env, ticket_id) == "done"


def test_governor_deny_defers_without_claiming_or_spawning(tmp_path):
    # wq#639: governor gate must precede claim (design §3 step 1), same as the
    # dep-preflight above — a denied dispatch should never touch the claim.
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(tmp_path, 0, "success")
    env["FAKE_GOVERNOR_DENY"] = "always"
    ticket_id = wq_add(env, "scratch governor-deny ticket")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "success")
    assert result.returncode == 11, result.stdout + result.stderr  # EXIT_DEFERRED
    assert "DEFERRED" in result.stdout

    assert wq_status(env, ticket_id) == "open"
    assert not os.path.isdir(os.path.join(worktrees_root, f"wq{ticket_id}-a1"))
    run_dir = os.path.join(runs_root, f"wq{ticket_id}")
    assert not os.path.isdir(run_dir) or not any(
        f.startswith("attempt-") for f in os.listdir(run_dir))


def test_governor_deny_after_claim_releases_ticket(tmp_path):
    # wq#639 repro: governor allows the pre-claim gate (call 1) but denies the
    # per-attempt gate inside do_attempt (call 2, now that the ticket is
    # claimed) — the claim must not be left held (was: fworker exited DEFERRED
    # holding the lease, wq#626 13:41:14Z).
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(tmp_path, 0, "success")
    env["FAKE_GOVERNOR_DENY"] = "after:1"
    env["FAKE_GOVERNOR_COUNTER_FILE"] = str(tmp_path / "governor_calls")
    ticket_id = wq_add(env, "scratch governor-deny-after-claim ticket")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "success")
    assert result.returncode == 11, result.stdout + result.stderr  # EXIT_DEFERRED
    assert "DEFERRED" in result.stdout

    assert wq_status(env, ticket_id) == "open", "claim must be released, not held, on deny"
    assert not os.path.isdir(os.path.join(worktrees_root, f"wq{ticket_id}-a1"))


def test_resume_after_near_term_limit_reuses_worktree_and_closes(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(
        tmp_path, 0, "rejected_limit_near")
    ticket_id = wq_add(env, "scratch near-limit ticket")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "rejected_limit_near")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_status(env, ticket_id) == "done"

    # same worktree reused across the LIMIT->resume boundary, no attempt-2 worktree.
    assert os.path.isdir(os.path.join(worktrees_root, f"wq{ticket_id}-a1"))
    assert not os.path.isdir(os.path.join(worktrees_root, f"wq{ticket_id}-a2"))


def test_worktree_collision_from_prior_crashed_attempt_is_adopted(tmp_path):
    # wq#649 repro (fworker-626c): a prior attempt got as far as `git worktree add`
    # succeeding before the whole fworker process died BEFORE write_attempt_state ran
    # (which only happens after the child exits) — so a fresh invocation recomputes the
    # SAME attempt number (next_attempt_n counts written attempt-N.json files) and
    # make_worktree collides on its own leftover branch. Must adopt gracefully, not
    # raise (which would crash before the claim was ever released).
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(tmp_path, 0, "success")
    title = "scratch collision ticket"
    ticket_id = wq_add(env, title)
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    branch = f"chore/wq{ticket_id}-{fworker.slugify(title)}-a1"
    wt = os.path.join(worktrees_root, f"wq{ticket_id}-a1")
    os.makedirs(worktrees_root, exist_ok=True)
    subprocess.run(["git", "-C", repo, "worktree", "add", wt, "-b", branch, "HEAD"],
                    capture_output=True, text=True, check=True)

    result = run_fworker(env, ticket_id, repo, "success", archetype="mechanical-edit")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_status(env, ticket_id) == "done"

    # adopted the pre-existing worktree/branch — still on the exact branch the
    # collision left behind, not a renamed/uniqued one.
    assert os.path.isdir(wt)
    cur_branch = subprocess.run(["git", "-C", wt, "rev-parse", "--abbrev-ref", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
    assert cur_branch == branch


def test_worktree_dir_from_unrelated_branch_is_replaced_not_crashed(tmp_path):
    # a directory sits at the expected worktree path but isn't ours (wrong branch, or
    # left by something else entirely) — must be cleared and replaced, not fatal.
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(tmp_path, 0, "success")
    title = "scratch foreign-dir ticket"
    ticket_id = wq_add(env, title)
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    wt = os.path.join(worktrees_root, f"wq{ticket_id}-a1")
    os.makedirs(worktrees_root, exist_ok=True)
    subprocess.run(["git", "-C", repo, "worktree", "add", wt, "-b", "chore/unrelated-branch",
                    "HEAD"], capture_output=True, text=True, check=True)

    result = run_fworker(env, ticket_id, repo, "success", archetype="mechanical-edit")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_status(env, ticket_id) == "done"

    cur_branch = subprocess.run(["git", "-C", wt, "rev-parse", "--abbrev-ref", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
    assert cur_branch == f"chore/wq{ticket_id}-{fworker.slugify(title)}-a1"


def test_unrecoverable_worktree_failure_releases_claim_not_dangling(tmp_path):
    # wq#649 §D: a make_worktree failure that even the pid-uniqued last resort can't
    # recover from (here: --repo points at something that isn't a git repo at all) must
    # not leave the wq claim dangling — cmd_run's try/except releases it immediately
    # rather than leaving it for the lease reaper to reclaim later.
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(tmp_path, 0, "success")
    ticket_id = wq_add(env, "scratch unrecoverable ticket")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)
    not_a_repo = str(tmp_path / "not_a_git_repo")
    os.makedirs(not_a_repo, exist_ok=True)

    result = run_fworker(env, ticket_id, not_a_repo, "success", archetype="mechanical-edit")
    assert result.returncode == fworker.EXIT_CRASH, result.stdout + result.stderr
    assert "CRASH" in result.stdout
    assert wq_status(env, ticket_id) == "open", "claim must be released, not held, on crash"
