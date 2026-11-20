#!/usr/bin/env python3
"""wq#5168: install.sh --daemon-only vs full install, run in a sandbox.

Every root-only command (install, systemctl, runuser, groupadd, useradd, usermod,
getent, setfacl, chown) is a shim that appends its argv to calls.log; `install`
only materialises files inside the sandbox. migrate() sources come from a fake
tree via TE_SRC_ROOT, so no real secret is read or copied. Nothing outside the
temp dir is written.
"""
import os, shutil, subprocess, tempfile, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # token-engine/
REPO = os.path.dirname(HERE)

SHIM = r'''#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$SANDBOX/calls.log"
name=$(basename "$0")
case "$name" in
  getent) exit 0 ;;
  runuser)
    for a in "$@"; do [ "$a" = cat ] && { echo "cat: canary: Permission denied" >&2; exit 1; }; done
    exit 0 ;;
  systemctl)
    case "$1" in
      is-enabled) [ "$2" = remote-credential-sweep.timer ] && { [ -n "$FAKE_SWEEP" ] && echo "$FAKE_SWEEP"; [ "$FAKE_SWEEP" = enabled ]; exit; }; echo enabled ;;
      is-active) echo active ;;
    esac
    exit 0 ;;
  install)
    dir=0; args=()
    while [ $# -gt 0 ]; do
      case "$1" in -m|-o|-g) shift 2 ;; -d) dir=1; shift ;; -D) shift ;; *) args+=("$1"); shift ;; esac
    done
    if [ $dir = 1 ]; then for d in "${args[@]}"; do case "$d" in "$SANDBOX"/*) mkdir -p "$d" ;; esac; done; exit 0; fi
    src=${args[0]}; dst=${args[-1]}
    case "$dst" in "$SANDBOX"/*) mkdir -p "$(dirname "$dst")"; cp "$src" "$dst" ;; esac
    exit 0 ;;
esac
exit 0
'''


class Sandbox:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="te-inst-")
        self.shims = os.path.join(self.tmp, "shims"); os.mkdir(self.shims)
        for n in ("install", "systemctl", "runuser", "groupadd", "useradd", "usermod",
                  "getent", "setfacl", "chown", "sleep"):
            p = os.path.join(self.shims, n)
            with open(p, "w") as f: f.write(SHIM)
            os.chmod(p, 0o755)
        # source checkout: token-engine/ + a fake sibling sweep bundle
        self.src = os.path.join(self.tmp, "repo", "token-engine")
        shutil.copytree(HERE, self.src, ignore=shutil.ignore_patterns("__pycache__", "tests"))
        sweep = os.path.join(self.tmp, "repo", "ops", "wq2243-remote-credential-sweep"); os.makedirs(sweep)
        with open(os.path.join(sweep, "install.sh"), "w") as f:
            f.write('echo "sweep-install $*" >> "$SANDBOX/calls.log"\n')
        open(os.path.join(sweep, "remote_credential_sweep.py"), "w").close()
        self.lib = os.path.join(self.tmp, "lib"); self.state = os.path.join(self.tmp, "state")
        self.units = os.path.join(self.tmp, "units"); os.mkdir(self.units)
        self.events = os.path.join(self.tmp, "events"); os.mkdir(self.events)
        self.root = os.path.join(self.tmp, "fake")       # TE_SRC_ROOT
        self.sock = os.path.join(self.tmp, "broker.sock"); open(self.sock, "w").close()
        self.policy = os.path.join(self.tmp, "fleet_exec_policy.py")
        with open(self.policy, "w") as f: f.write("# policy\n")

    def fake_secret(self, rel, body):
        p = os.path.join(self.root, rel.lstrip("/")); os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f: f.write(body)
        return p

    def run(self, *args, **env):
        e = dict(os.environ, PATH=self.shims + os.pathsep + os.environ["PATH"], SANDBOX=self.tmp,
                 TE_LIB=self.lib, TE_STATE=self.state, TE_EVENTS=self.events, TE_SOCK=self.sock,
                 TE_UNIT_DIR=self.units, TE_POLICY_SRC=self.policy, TE_SRC_ROOT=self.root,
                 FAKE_SWEEP=env.pop("FAKE_SWEEP", ""))
        for k in ("TE_ENABLE_SWEEP_TIMER", "TE_MIGRATE_OVERWRITE", "TE_FINALIZE_MOVE",
                  "LITELLM_ADMIN_KEY", "CLOUDFLARE_TOKEN_SRC"):
            e.pop(k, None)
        e.update(env)
        out = subprocess.run(["bash", os.path.join(self.src, "install.sh"), *args, self.src],
                             capture_output=True, text=True, timeout=60, env=e)
        log = open(os.path.join(self.tmp, "calls.log")).read() if os.path.exists(os.path.join(self.tmp, "calls.log")) else ""
        return out, log

    def reset_log(self):
        p = os.path.join(self.tmp, "calls.log")
        if os.path.exists(p): os.remove(p)

    def close(self): shutil.rmtree(self.tmp, ignore_errors=True)


class InstallModesTest(unittest.TestCase):
    def setUp(self): self.sb = Sandbox()
    def tearDown(self): self.sb.close()

    def full(self, **env):
        out, log = self.sb.run(**env)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out, log

    def test_daemon_only_refuses_without_first_install(self):
        out, log = self.sb.run("--daemon-only")
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assertIn("REFUSED", out.stderr)
        self.assertNotIn("systemctl restart", log)

    def test_daemon_only_redeploys_code_and_nothing_else(self):
        self.full(FAKE_SWEEP="enabled")
        self.sb.reset_log()
        with open(os.path.join(self.sb.src, "token_engine_daemon.py"), "a") as f: f.write("\n# v2\n")
        out, log = self.sb.run("--daemon-only")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue(open(os.path.join(self.sb.lib, "token_engine_daemon.py")).read().endswith("# v2\n"))
        for f in ("token_ledger.py", "te_client.py", "fleet_exec_policy.py", "forges.json", "providers.json"):
            self.assertTrue(os.path.exists(os.path.join(self.sb.lib, f)), f)
        self.assertIn("systemctl restart token-engine.service", log)
        self.assertIn("check_deploy_drift.py", log)
        self.assertIn("OK: uid lumen -> EACCES", out.stdout)
        for forbidden in ("groupadd", "useradd", "usermod", "setfacl", "chown", "sweep-install",
                          "remote_credential_sweep", "daemon-reload", "systemctl enable",
                          "git-credential-token-engine", "hooks/pre-push"):
            self.assertNotIn(forbidden, log, forbidden)
        self.assertNotIn("copied ", out.stdout)

    def test_full_install_leaves_disabled_sweep_timer_alone(self):
        out, log = self.full(FAKE_SWEEP="disabled")
        self.assertNotIn("sweep-install", log)
        self.assertIn("not re-enabled", out.stderr)
        self.assertIn("remote_credential_sweep.py scan --fix", log)   # one-off strip still runs

    def test_full_install_masked_timer_also_left_alone(self):
        out, log = self.full(FAKE_SWEEP="masked")
        self.assertNotIn("sweep-install", log)

    def test_sweep_flag_enables(self):
        out, log = self.full(FAKE_SWEEP="disabled", TE_ENABLE_SWEEP_TIMER="1")
        self.assertIn("sweep-install", log)

    def test_sweep_installed_when_enabled_or_absent(self):
        for st in ("enabled", ""):
            self.sb.reset_log()
            out, log = self.full(FAKE_SWEEP=st)
            self.assertIn("sweep-install", log, st)

    def test_migrate_keeps_differing_store_copy(self):
        src = self.sb.fake_secret("/home/lumen/.secrets/gitea-lumen.token", "old-source\n")
        self.full(FAKE_SWEEP="enabled")
        store = os.path.join(self.sb.state, "secrets", "gitea-lumen.token")
        self.assertEqual(open(store).read(), "old-source\n")         # first install copies
        with open(store, "w") as f: f.write("rotated-in-store\n")      # store-side rotation
        out, _ = self.full(FAKE_SWEEP="enabled")
        self.assertEqual(open(store).read(), "rotated-in-store\n")
        self.assertIn("KEEP store", out.stdout)
        out, _ = self.full(FAKE_SWEEP="enabled", TE_MIGRATE_OVERWRITE="1")
        self.assertEqual(open(store).read(), "old-source\n")
        self.assertTrue(os.path.exists(src))

    def test_migrate_identical_copy_is_quiet_noop(self):
        self.sb.fake_secret("/home/lumen/.config/tapo.env", "same\n")
        self.full(FAKE_SWEEP="enabled")
        out, _ = self.full(FAKE_SWEEP="enabled")
        self.assertNotIn("KEEP store", out.stdout)
        self.assertIn("copied", out.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
