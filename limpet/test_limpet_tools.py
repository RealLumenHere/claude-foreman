"""End-to-end tests for the limpet-tools CLI: real git repos, fail-closed install.

Falsifiability (wq#2630): the plugin and its index are real git repos under
tmp_path; install really clones and checks out the pinned rev. Every refusal
probe asserts the NEGATIVE space too -- nothing written to the plugin store,
no receipt, no drop-in -- so an installer that "refused" after writing would
fail. The signature fake is keyed (sha256 over key+payload): a signature made
with the wrong key, or an index that lies about the tree hash, genuinely
fails to verify. All dirs are pinned outside the /etc and /var defaults.
"""
import hashlib
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import limpet_tools  # noqa: E402
import registry_trust  # noqa: E402
from plugin_registry import Registry  # noqa: E402


def fake_sign(payload: bytes, key: str) -> str:
    return hashlib.sha256(key.encode() + payload).hexdigest()


def fake_verify(payload: bytes, sig: str, key: str) -> bool:
    return sig == fake_sign(payload, key)


@pytest.fixture(autouse=True)
def _fake_minisign(monkeypatch):
    monkeypatch.setattr(registry_trust, "minisign_verify", fake_verify)


def git(args, cwd):
    proc = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


MANIFEST = """\
[plugin]
name = "ubertooth"
version = "2.1.0"
kind = "tool"
"""

ESCALATING_MANIFEST = """\
[plugin]
name = "flashrom"
version = "1.0"
kind = "tool"

[mutation]
escalates = true
writes = ["device"]
"""


def make_plugin_repo(path, manifest=MANIFEST):
    os.makedirs(path)
    git(["init", "-q"], path)
    git(["config", "user.email", "t@example.invalid"], path)
    git(["config", "user.name", "t"], path)
    with open(os.path.join(path, "limpet-plugin.toml"), "w", encoding="utf-8") as fh:
        fh.write(manifest)
    with open(os.path.join(path, "install.sh"), "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    git(["add", "-A"], path)
    git(["commit", "-qm", "plugin"], path)
    return git(["rev-parse", "HEAD"], path)


def make_index_repo(path, records):
    os.makedirs(path)
    git(["init", "-q"], path)
    git(["config", "user.email", "t@example.invalid"], path)
    git(["config", "user.name", "t"], path)
    with open(os.path.join(path, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(records, fh)
    git(["add", "index.json"], path)
    git(["commit", "-qm", "index"], path)


def listed_record(name, repo_path, rev, key="authorkey", tier="community",
                  tree_hash=None, **kw):
    th = tree_hash or registry_trust.tree_hash(repo_path)
    payload = registry_trust.signing_payload(name, repo_path, rev, th)
    rec = {"name": name, "namespace": f"io.github.a.{name}", "version": "2.1.0",
           "category": "hardware-interface", "description": f"{name} sniffer",
           "license": "GPL-2.0", "git": repo_path, "rev": rev, "tier": tier,
           "tags": ["bluetooth"],
           "signature": {"scheme": "minisign", "pubkey_id": "RWTx",
                         "pubkey": key, "sig": fake_sign(payload, key),
                         "tree_hash": th}}
    rec.update(kw)
    return rec


@pytest.fixture
def env(tmp_path):
    """A provisioned box: one plugin repo, one canonical index listing it,
    the index added+synced, all dirs pinned under tmp_path."""
    plugin_repo = str(tmp_path / "upstream" / "ubertooth")
    rev = make_plugin_repo(plugin_repo)
    index_repo = str(tmp_path / "upstream" / "index")
    make_index_repo(index_repo, [listed_record("ubertooth", plugin_repo, rev)])
    dirs = {
        "state": str(tmp_path / "state"),
        "plugins": str(tmp_path / "plugins.d"),
        "trust": str(tmp_path / "trust"),
        "plugin_repo": plugin_repo, "index_repo": index_repo, "rev": rev,
    }
    assert tools(dirs, "index", "add", index_repo, "--name", "canonical",
                 "--ceiling", "core") == 0
    return dirs


def tools(dirs, *argv):
    return limpet_tools.main([
        "--state-dir", dirs["state"], "--plugins-dir", dirs["plugins"],
        "--trust-dir", dirs["trust"], *argv])


def test_search_and_info_from_local_clone(env, capsys):
    assert tools(env, "--json", "search", "bluetooth") == 0
    [rec] = json.loads(capsys.readouterr().out)["results"]
    assert rec["name"] == "ubertooth" and rec["index"] == "canonical"
    assert tools(env, "--json", "info", "ubertooth") == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["rev"] == env["rev"] and shown["installed"] is False
    assert tools(env, "info", "ghost") == 1


def test_install_listed_plugin_end_to_end(env, capsys):
    assert tools(env, "--json", "install", "ubertooth") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["installed"] == "ubertooth" and out["rev"] == env["rev"]
    receipt = registry_trust.load_receipt(env["state"], "ubertooth")
    assert receipt.tier == "community" and receipt.rev == env["rev"]
    assert os.path.exists(os.path.join(env["plugins"], "ubertooth.toml"))

    # The registry's default load gate re-verifies and loads it.
    reg = Registry(plugins_dir=env["plugins"], state_dir=env["state"],
                   trust_gate=registry_trust.trust_gate(
                       env["state"], trust_dir=env["trust"], verifier=fake_verify))
    assert "ubertooth" in reg.scan().loaded

    # verify agrees now...
    assert tools(env, "verify", "ubertooth") == 0
    # ...and a post-install tamper fails BOTH re-verify paths (spec §5.2.4).
    with open(os.path.join(receipt.tree, "install.sh"), "a") as fh:
        fh.write("echo backdoor\n")
    assert tools(env, "verify", "ubertooth") == 2
    report = reg.scan()
    assert "ubertooth" not in report.loaded
    assert any("tree hash mismatch" in e for errs in report.rejected.values() for e in errs)


def nothing_installed(env):
    store = os.path.join(env["state"], "plugins")
    no_store = not os.path.isdir(store) or not [
        d for d in os.listdir(store) if not d.startswith(".")]
    return (no_store
            and registry_trust.load_receipt(env["state"], "ubertooth") is None
            and not os.path.exists(os.path.join(env["plugins"], "ubertooth.toml")))


def test_install_refuses_lying_tree_hash(env, tmp_path):
    """An index that pins a tree hash the actual rev does not produce (the
    content moved after signing) must abort with nothing written."""
    bad = listed_record("ubertooth", env["plugin_repo"], env["rev"],
                        tree_hash="sha256:" + "0" * 64)
    make_index_repo(str(tmp_path / "bad-index"), [bad])
    assert tools(env, "index", "remove", "canonical") == 0
    assert tools(env, "index", "add", str(tmp_path / "bad-index"),
                 "--name", "canonical") == 0
    assert tools(env, "install", "ubertooth") == 2
    assert nothing_installed(env)


def test_install_refuses_wrong_signer(env, tmp_path):
    rec = listed_record("ubertooth", env["plugin_repo"], env["rev"], key="authorkey")
    rec["signature"]["pubkey"] = "impostorkey"  # sig was made with authorkey
    make_index_repo(str(tmp_path / "forged-index"), [rec])
    assert tools(env, "index", "remove", "canonical") == 0
    assert tools(env, "index", "add", str(tmp_path / "forged-index"),
                 "--name", "canonical") == 0
    assert tools(env, "install", "ubertooth") == 2
    assert nothing_installed(env)


def test_install_refuses_unsigned_listing(env, tmp_path):
    rec = listed_record("ubertooth", env["plugin_repo"], env["rev"])
    del rec["signature"]
    make_index_repo(str(tmp_path / "unsigned-index"), [rec])
    assert tools(env, "index", "remove", "canonical") == 0
    assert tools(env, "index", "add", str(tmp_path / "unsigned-index"),
                 "--name", "canonical") == 0
    assert tools(env, "install", "ubertooth") == 2
    assert nothing_installed(env)


def test_direct_git_install_lands_at_unlisted_floor(env, capsys):
    url = env["plugin_repo"] + "/.git"
    assert tools(env, "--json", "install", url, "--name", "ubertooth") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["tier"] == "unlisted" and out["warnings"]
    receipt = registry_trust.load_receipt(env["state"], "ubertooth")
    assert receipt.tier == "unlisted" and receipt.sig == ""
    assert tools(env, "verify", "ubertooth") == 0  # hash-pinned even unsigned


def test_unsafe_name_rejected_before_any_write(env, capsys):
    url = env["plugin_repo"] + "/.git"
    assert tools(env, "install", url, "--name", "../../etc/cron.d/evil") == 2
    assert nothing_installed(env)
    err = capsys.readouterr().err
    assert "unsafe plugin name" in err and "nothing was written" in err


def test_escalating_community_plugin_needs_elevate(env, tmp_path):
    plugin_repo = str(tmp_path / "upstream" / "flashrom")
    rev = make_plugin_repo(plugin_repo, ESCALATING_MANIFEST)
    make_index_repo(str(tmp_path / "upstream" / "index2"),
                    [listed_record("flashrom", plugin_repo, rev)])
    assert tools(env, "index", "add", str(tmp_path / "upstream" / "index2"),
                 "--name", "extra") == 0
    assert tools(env, "install", "flashrom") == 0
    gate = registry_trust.trust_gate(env["state"], trust_dir=env["trust"],
                                     verifier=fake_verify)
    reg = Registry(plugins_dir=env["plugins"], state_dir=env["state"], trust_gate=gate)
    report = reg.scan()
    assert "flashrom" not in report.loaded  # §7.2: community + escalates
    assert any("tier policy" in e for errs in report.rejected.values() for e in errs)
    assert tools(env, "elevate", "flashrom") == 0
    assert "flashrom" in reg.scan().loaded
    assert tools(env, "elevate", "flashrom", "--revoke") == 0
    assert "flashrom" not in reg.scan().loaded


def test_install_unknown_name_without_index_entry(env):
    assert tools(env, "install", "ghost") == 1
    assert tools(env, "verify", "ghost") == 1
    assert tools(env, "elevate", "ghost") == 1
