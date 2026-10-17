"""Tests for registry_index.py: federated git-repo indexes, local-first search.

Falsifiability (wq#2630): index repos here are REAL git repos built in
tmp_path -- sync clones and pulls them for real, and the offline probe
deletes the upstream after a sync so a `search` that secretly needed the
network would fail, not pass. The ranking probe gives the community plugin
strictly better popularity stats than the vetted one, so tier-first ranking
(the ClawHub popularity-is-not-trust rule, spec §5.3) is the only way the
test can pass. State dirs are pinned to tmp_path, outside /var/lib/limpet.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import registry_index  # noqa: E402
from registry_index import (  # noqa: E402
    add_index, clamp_tier, entry_to_record, generate_index, info, load_config,
    load_records, namespace_errors, remove_index, search, sync_index,
    validate_entry, write_index,
)


def git(args, cwd):
    proc = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def make_index_repo(path, records):
    os.makedirs(path)
    git(["init", "-q"], path)
    git(["config", "user.email", "test@example.invalid"], path)
    git(["config", "user.name", "test"], path)
    with open(os.path.join(path, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(records, fh)
    git(["add", "index.json"], path)
    git(["commit", "-qm", "index"], path)
    return path


def record(name, tier="community", **kw):
    rec = {"name": name, "namespace": f"io.github.someone.{name}", "version": "1.0",
           "category": "hardware-interface", "description": f"{name} tool",
           "license": "MIT", "git": f"https://example.invalid/{name}.git",
           "rev": "a" * 40, "tier": tier, "tags": [], "stats": {}}
    rec.update(kw)
    return rec


# ---- config ----------------------------------------------------------------
def test_add_remove_list_indexes(tmp_path):
    state = str(tmp_path / "state")
    add_index(state, "canonical", "https://example.invalid/i.git", ceiling="core")
    with pytest.raises(ValueError):
        add_index(state, "canonical", "https://example.invalid/other.git")
    with pytest.raises(ValueError):
        add_index(state, "bad", "url", ceiling="unlisted")
    with pytest.raises(ValueError):
        add_index(state, "../escape", "url")
    assert [e["name"] for e in load_config(state)] == ["canonical"]
    assert remove_index(state, "canonical")
    assert not remove_index(state, "canonical")
    assert load_config(state) == []


def test_removed_index_stops_answering_and_readd_starts_clean(tmp_path):
    """remove must drop the local clone too: a removed index that kept
    answering searches -- or leaked its records into a NEW index added under
    the same name -- would let a stale/forged catalog outlive its removal."""
    state = str(tmp_path / "state")
    old = make_index_repo(str(tmp_path / "old"), [record("ubertooth")])
    entry = add_index(state, "canonical", old, ceiling="core")
    assert sync_index(state, entry)[0]
    assert {r["name"] for r in load_records(state)} == {"ubertooth"}
    assert remove_index(state, "canonical")
    assert load_records(state) == []
    new = make_index_repo(str(tmp_path / "new"), [record("sigrok")])
    entry = add_index(state, "canonical", new, ceiling="core")
    assert sync_index(state, entry)[0]
    assert {r["name"] for r in load_records(state)} == {"sigrok"}


def test_clamp_tier_fail_closed():
    assert clamp_tier("core", "core") == "core"
    assert clamp_tier("core", "community") == "community"   # no self-promotion
    assert clamp_tier("community", "core") == "community"   # ceiling never promotes
    assert clamp_tier("made-up", "core") == "unlisted"
    assert clamp_tier("core", "made-up") == "unlisted"


# ---- sync: online, then offline against the last sync ----------------------
def test_sync_then_offline_search_uses_last_sync(tmp_path):
    state = str(tmp_path / "state")
    upstream = make_index_repo(str(tmp_path / "upstream"), [record("ubertooth")])
    entry = add_index(state, "canonical", upstream, ceiling="core")
    synced, _ = sync_index(state, entry)
    assert synced
    assert load_config(state)[0]["last_synced"] is not None

    # Upstream grows a plugin; a pull picks it up.
    with open(os.path.join(upstream, "index.json"), "w", encoding="utf-8") as fh:
        json.dump([record("ubertooth"), record("hackrf")], fh)
    git(["commit", "-aqm", "add hackrf"], upstream)
    synced, _ = sync_index(state, entry)
    assert synced
    assert {r["name"] for r in load_records(state)} == {"ubertooth", "hackrf"}

    # Offline probe: upstream vanishes entirely. Sync degrades, search still
    # answers from the local clone (spec §4.2: air-gapped discovery).
    shutil.rmtree(upstream)
    synced, detail = sync_index(state, entry)
    assert not synced and "last sync" in detail
    assert {r["name"] for r in load_records(state)} == {"ubertooth", "hackrf"}


# ---- merged records ---------------------------------------------------------
def test_load_records_clamps_tier_and_first_index_wins(tmp_path):
    state = str(tmp_path / "state")
    canonical = make_index_repo(str(tmp_path / "canonical"),
                                [record("ubertooth", tier="vetted")])
    third = make_index_repo(
        str(tmp_path / "third"),
        [record("ubertooth", tier="core", description="shadowing attempt"),
         record("sigrok", tier="core"),           # claims core; ceiling clamps
         record("../evil", tier="community")])    # unsafe name is dropped
    for name, url, ceiling in (("canonical", canonical, "core"),
                               ("third", third, "community")):
        entry = add_index(state, name, url, ceiling=ceiling)
        assert sync_index(state, entry)[0]
    records = {r["name"]: r for r in load_records(state)}
    assert set(records) == {"ubertooth", "sigrok"}
    assert records["ubertooth"]["index"] == "canonical"       # not shadowed
    assert records["ubertooth"]["tier"] == "vetted"
    assert records["sigrok"]["tier"] == "community"           # clamped


# ---- search ranking + filters ----------------------------------------------
def test_search_ranks_tier_above_popularity():
    vetted = record("ubertooth", tier="vetted",
                    tags=["bluetooth"], stats={"stars": 1, "installs": 2})
    popular = record("blue-sniff", tier="community",
                     tags=["bluetooth"], stats={"stars": 99999, "installs": 99999})
    assert [r["name"] for r in search([popular, vetted], "bluetooth")] == [
        "ubertooth", "blue-sniff"]


def test_search_filters_and_relevance():
    records = [
        record("ubertooth", tags=["bluetooth", "ble"], hardware_required=["usb:1d50:6002"]),
        record("hackrf", tags=["sdr"], category="sdr"),
        record("sigrok", tags=["logic-analyzer"]),
    ]
    assert [r["name"] for r in search(records, "bluetooth")] == ["ubertooth"]
    assert [r["name"] for r in search(records, "", category="sdr")] == ["hackrf"]
    assert [r["name"] for r in search(records, "", tag="ble")] == ["ubertooth"]
    assert [r["name"] for r in search(records, "", hardware="usb:1d50:6002")] == ["ubertooth"]
    assert len(search(records, "")) == 3  # no terms = browse everything
    assert search(records, "no-such-thing") == []
    assert info(records, "hackrf")["name"] == "hackrf"
    assert info(records, "ghost") is None


# ---- namespaces (spec §2) ----------------------------------------------------
def test_namespace_rules():
    assert namespace_errors("io.github.alice.ubertooth", "ubertooth", "community") == []
    assert namespace_errors("dev.limpet.binwalk", "binwalk", "core") == []
    assert namespace_errors("not-reverse-dns", "x", "community")
    assert namespace_errors("io.github.alice.other", "ubertooth", "community")
    # A community submission cannot squat the project namespace.
    assert any("project-reserved" in e
               for e in namespace_errors("dev.limpet.evil", "evil", "community"))


# ---- index generation (the index repo's CI, spec §3/§5.4) --------------------
GOOD_ENTRY = """
[plugin]
name = "ubertooth"
version = "2.1.0"
category = "hardware-interface"
description = "Ubertooth One BLE/BT sniffer"
license = "GPL-2.0"

[registry]
namespace = "io.github.someuser.ubertooth"
git = "https://example.invalid/limpet-plugin-ubertooth.git"
rev = "{rev}"
tier = "community"
tags = ["bluetooth", "ble"]

[signature]
scheme = "minisign"
pubkey_id = "RWTtest"
pubkey = "authorkey"
sig = "detached-sig"
tree_hash = "sha256:abc"
""".format(rev="a" * 40)


def write_entry(dirpath, fname, text):
    os.makedirs(dirpath, exist_ok=True)
    with open(os.path.join(dirpath, fname), "w", encoding="utf-8") as fh:
        fh.write(text)


def test_generate_index_good_entry(tmp_path):
    entries = str(tmp_path / "entries")
    write_entry(entries, "ubertooth.toml", GOOD_ENTRY)
    records, errors = generate_index(entries)
    assert errors == {}
    [rec] = records
    assert rec["name"] == "ubertooth"
    assert rec["signature"]["tree_hash"] == "sha256:abc"


@pytest.mark.parametrize("mangle,expect", [
    (lambda s: s.replace('license = "GPL-2.0"\n', ""), "license"),
    (lambda s: s.replace("a" * 40, "main"), "full commit SHA"),
    (lambda s: s.replace("io.github.someuser.ubertooth", "dev.limpet.ubertooth"),
     "project-reserved"),
    (lambda s: s.replace('tier = "community"', 'tier = "unlisted"'), "tier"),
    (lambda s: s.replace('sig = "detached-sig"\n', ""), "signature"),
])
def test_generate_index_rejects_bad_entries(tmp_path, mangle, expect):
    entries = str(tmp_path / "entries")
    write_entry(entries, "ubertooth.toml", mangle(GOOD_ENTRY))
    records, errors = generate_index(entries)
    assert records == []
    assert any(expect in e for e in errors["ubertooth.toml"])


def test_generate_index_runs_ownership_check(tmp_path):
    entries = str(tmp_path / "entries")
    write_entry(entries, "ubertooth.toml", GOOD_ENTRY)
    checked = []

    def deny(namespace, entry):
        checked.append(namespace)
        return "no DNS TXT record / OAuth proof"

    records, errors = generate_index(entries, ownership_check=deny)
    assert checked == ["io.github.someuser.ubertooth"]
    assert records == [] and "ubertooth.toml" in errors


def test_write_index_aborts_on_any_error(tmp_path):
    entries = str(tmp_path / "entries")
    write_entry(entries, "good.toml", GOOD_ENTRY)
    write_entry(entries, "bad.toml", "not toml at all [")
    out = str(tmp_path / "index.json")
    _, errors = write_index(entries, out)
    assert errors and not os.path.exists(out)  # CI gate: bad entry, no publish
    os.unlink(os.path.join(entries, "bad.toml"))
    records, errors = write_index(entries, out)
    assert errors == {} and json.load(open(out)) == records


def test_duplicate_entry_names_rejected(tmp_path):
    entries = str(tmp_path / "entries")
    write_entry(entries, "a.toml", GOOD_ENTRY)
    write_entry(entries, "b.toml", GOOD_ENTRY)
    records, errors = generate_index(entries)
    assert len(records) == 1 and any("duplicate" in e for e in errors["b.toml"])


def test_validate_entry_missing_tables():
    assert validate_entry({}) == ["missing [plugin] table"]
    assert validate_entry({"plugin": {}}) == ["missing [registry] table"]


# ---- per-tool energize override (spec §5.4, wq#4146) ---------------------
# Falsifiability: the gated tool (flash_write) and the ungated tool
# (jtag_scan) are BOTH on a plugin whose plugin-wide needs_energize is False,
# so the record carrying tool_energize["flash_write"] == True is the only
# way the per-tool override reaches the index -- a schema that still only
# emitted the plugin-wide flag would drop it and fail this assertion.
GOOD_ENTRY_ENERGIZE = GOOD_ENTRY.replace(
    'tags = ["bluetooth", "ble"]',
    'tags = ["jtag"]\nneeds_energize = false\n\n'
    '[registry.tool_energize]\njtag_scan = false\nflash_write = true')


def test_tool_energize_override_reaches_record(tmp_path):
    entries = str(tmp_path / "entries")
    write_entry(entries, "ubertooth.toml", GOOD_ENTRY_ENERGIZE)
    records, errors = generate_index(entries)
    assert errors == {}
    [rec] = records
    assert rec["needs_energize"] is False          # plugin-wide default
    assert rec["tool_energize"] == {               # per-tool overrides win
        "jtag_scan": False, "flash_write": True}


def test_tool_energize_defaults_empty_when_absent():
    rec = entry_to_record({
        "plugin": {"name": "x", "version": "1", "category": "c",
                   "description": "d", "license": "MIT"},
        "registry": {"namespace": "io.github.u.x", "git": "g",
                     "rev": "a" * 40, "tier": "community"}})
    assert rec["tool_energize"] == {}


def test_tool_energize_rejects_non_boolean():
    errors = validate_entry({
        "plugin": {"name": "x", "version": "1", "category": "c",
                   "description": "d", "license": "MIT"},
        "registry": {"namespace": "io.github.u.x", "git": "g",
                     "rev": "a" * 40, "tier": "community",
                     "tool_energize": {"flash_write": "yes"}}})
    assert any("tool_energize" in e and "boolean" in e for e in errors)
