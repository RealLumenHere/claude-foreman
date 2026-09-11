import json
import os
import sqlite3
import sys

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import storage_manifest_drift as smd  # noqa: E402

# Shapes copied from a live `lsblk -J` / `findmnt -J` on lumen and the committed
# manifest's nephthys block (2026-09-11).
MEMBER_UUID = "8716cdfb-daba-59e6-49d2-9d2ed3b16e35"
MD0 = {"name": "md0", "type": "raid5", "serial": None, "model": None, "size": "476.7G",
       "fstype": "ext4", "uuid": "7233eb73-e110-4119-ae00-e536a4926695"}


def disk(name, serial, model="SAMSUNG MZFLV256HCHP-000MV", size="238.5G", children=None,
         fstype=None, uuid=None):
    return {"name": name, "type": "disk", "serial": serial, "model": model, "size": size,
            "fstype": fstype, "uuid": uuid, "children": children or []}


LSBLK = {"blockdevices": [
    disk("nvme0n1", "S245NAAGC69747", fstype="linux_raid_member", uuid=MEMBER_UUID,
         children=[MD0]),
    disk("nvme1n1", "S245NYAG834024", fstype="linux_raid_member", uuid=MEMBER_UUID,
         children=[MD0]),
    disk("nvme2n1", "S245NX0H768738", fstype="linux_raid_member", uuid=MEMBER_UUID,
         children=[MD0]),
    disk("sda", "ZDZRTG6X", model="ST2000LM015-2E81", size="1.8T", children=[
        {"name": "sda1", "type": "part", "serial": None, "model": None, "size": "1.8T",
         "fstype": "ext4", "uuid": "f33a2ae5-bf6b-4370-b822-ebd2aa659b3e"}]),
    {"name": "loop0", "type": "loop", "serial": None, "model": None, "size": "4K",
     "fstype": "squashfs", "uuid": None},
]}
FINDMNT = {"filesystems": [
    {"target": "/", "source": "/dev/nvme4n1p2", "fstype": "ext4", "uuid": "root-uuid",
     "children": [
         {"target": "/data/lumen", "source": "/dev/md0", "fstype": "ext4",
          "uuid": "7233eb73-e110-4119-ae00-e536a4926695"},
         {"target": "/snap/core/1", "source": "/dev/loop0", "fstype": "squashfs",
          "uuid": None},
         {"target": "/proc", "source": "proc", "fstype": "proc", "uuid": None},
         {"target": "/srv/bind", "source": "/dev/nvme4n1p2[/srv]", "fstype": "ext4",
          "uuid": "root-uuid"},
     ]},
]}

MANIFEST = {
    "meta": {"version": 1},
    "nodes": {
        "lumen": {
            "arrays": [{"name": "md0", "uuid": "8716cdfb:daba59e6:49d29d2e:d3b16e35",
                        "level": "raid5", "members": 3}],
            "devices": [
                {"model": "SAMSUNG MZFLV256HCHP-000MV", "serial": "S245NAAGC69747",
                 "size": "238.5G"},
                {"model": "SAMSUNG MZFLV256HCHP-000MV", "serial": "S245NYAG834024",
                 "size": "238.5G"},
                {"model": "SAMSUNG MZFLV256HCHP-000MV", "serial": "S245NX0H768738",
                 "size": "238.5G"},
                {"model": "ST2000LM015-2E81", "serial": "ZDZRTG6X", "size": "1.8T"},
            ],
            "optional_devices": ["ZDZRTG6X"],
            "governor": "performance",
            "mounts": [
                {"fstype": "ext4", "noauto": False, "required": True, "target": "/",
                 "uuid": "root-uuid"},
                {"fstype": "ext4", "noauto": False, "required": True,
                 "target": "/data/lumen", "uuid": "7233eb73-e110-4119-ae00-e536a4926695"},
                {"fstype": "ext4", "noauto": False, "required": True,
                 "target": "/srv/bind", "uuid": "root-uuid"},
            ],
        },
        "hathor": {"parked": True, "unreachable_ok": True},
    },
}


def survey_text(lsblk=LSBLK, findmnt=FINDMNT):
    return f"{json.dumps(lsblk)}\n{smd.SPLIT}\n{json.dumps(findmnt)}\n"


def live(**over):
    return {"status": "ok", **smd.parse_survey(survey_text(**over))}


def kinds(findings):
    return sorted((f["kind"], f["key"]) for f in findings)


# --------------------------------------------------------------------- survey

def test_md_uuid_forms_normalize_equal():
    assert smd.norm_md_uuid(MEMBER_UUID) == smd.norm_md_uuid("8716cdfb:daba59e6:49d29d2e:d3b16e35")
    assert smd.md_colon_form(MEMBER_UUID) == "8716cdfb:daba59e6:49d29d2e:d3b16e35"


def test_parse_lsblk_folds_repeated_md_and_skips_loop():
    s = smd.parse_survey(survey_text())
    assert [d["serial"] for d in s["devices"]] == [
        "S245NAAGC69747", "S245NX0H768738", "S245NYAG834024", "ZDZRTG6X"]
    assert s["arrays"] == [{"name": "md0", "uuid": "8716cdfb:daba59e6:49d29d2e:d3b16e35",
                            "level": "raid5", "members": 3}]


def test_parse_findmnt_keeps_block_mounts_only_and_strips_bind_suffix():
    targets = [m["target"] for m in smd.parse_survey(survey_text())["mounts"]]
    assert targets == ["/", "/data/lumen", "/srv/bind"]


# ----------------------------------------------------------------------- diff

def test_matching_survey_is_clean():
    assert smd.diff_node("lumen", MANIFEST["nodes"]["lumen"], live()) == []


def test_optional_device_absent_is_not_drift():
    lsblk = {"blockdevices": LSBLK["blockdevices"][:3]}
    assert smd.diff_node("lumen", MANIFEST["nodes"]["lumen"], live(lsblk=lsblk)) == []


def test_member_loss_reports_device_and_array_drift():
    lsblk = {"blockdevices": LSBLK["blockdevices"][:2] + LSBLK["blockdevices"][3:]}
    got = kinds(smd.diff_node("lumen", MANIFEST["nodes"]["lumen"], live(lsblk=lsblk)))
    assert got == [("array_members_changed", "md0"), ("device_missing", "S245NX0H768738")]


def test_new_disk_and_new_mount_are_undeclared():
    lsblk = {"blockdevices": LSBLK["blockdevices"] + [disk("sdb", "NEWSERIAL", size="3.6T")]}
    findmnt = json.loads(json.dumps(FINDMNT))
    findmnt["filesystems"][0]["children"].append(
        {"target": "/mnt/new", "source": "/dev/sdb1", "fstype": "xfs", "uuid": "u-new"})
    got = kinds(smd.diff_node("lumen", MANIFEST["nodes"]["lumen"],
                              live(lsblk=lsblk, findmnt=findmnt)))
    assert got == [("device_undeclared", "NEWSERIAL"), ("mount_undeclared", "/mnt/new")]


def test_required_mount_missing_and_uuid_changed():
    findmnt = json.loads(json.dumps(FINDMNT))
    kids = findmnt["filesystems"][0]["children"]
    kids[:] = [k for k in kids if k["target"] != "/data/lumen"]
    findmnt["filesystems"][0]["uuid"] = "other-root"
    got = kinds(smd.diff_node("lumen", MANIFEST["nodes"]["lumen"], live(findmnt=findmnt)))
    assert got == [("mount_missing", "/data/lumen"), ("mount_uuid_changed", "/")]


def test_optional_or_noauto_mount_absent_is_not_drift():
    declared = json.loads(json.dumps(MANIFEST["nodes"]["lumen"]))
    declared["mounts"] += [
        {"fstype": "ext4", "noauto": True, "required": False, "target": "/mnt/backup",
         "uuid": "u1"},
        {"fstype": "ext4", "noauto": False, "required": False, "target": "/mnt/old",
         "uuid": "u2"},
    ]
    assert smd.diff_node("lumen", declared, live()) == []


def test_array_level_change():
    lsblk = json.loads(json.dumps(LSBLK))
    for d in lsblk["blockdevices"][:3]:
        d["children"][0]["type"] = "raid6"
    got = kinds(smd.diff_node("lumen", MANIFEST["nodes"]["lumen"], live(lsblk=lsblk)))
    assert got == [("array_level_changed", "md0")]


LVM_ID = "dm-uuid-LVM-oDyh71yYVCteDUlTmAzzHqGeI6Rv3WEsUXxv0Ea8l45ep9OSPTONkA338HX2LTEU"


def lvm_root_node():
    declared = json.loads(json.dumps(MANIFEST["nodes"]["lumen"]))
    declared["mounts"][0]["uuid"] = smd.BYID_DIR + LVM_ID
    return declared


def with_by_id(survey, names):
    return {**survey, "by_id": names}


def test_by_id_mount_verified_by_identity_not_fs_uuid():
    # Live lumen/anubis/sobek: root declared by LVM dm-uuid, findmnt reports FS-UUID.
    assert smd.diff_node("lumen", lvm_root_node(), with_by_id(live(), [LVM_ID])) == []
    got = kinds(smd.diff_node("lumen", lvm_root_node(), with_by_id(live(), [])))
    assert got == [("mount_device_missing", "/")]


def test_survey_parses_by_id_section():
    text = survey_text() + f"{smd.BYID_SPLIT}\n{LVM_ID}\nnvme-foo\n"
    assert smd.parse_survey(text)["by_id"] == [LVM_ID, "nvme-foo"]


def test_placeholder_serial_is_unverifiable_and_kept():
    # Live apep: gen_manifest wrote serial UNKNOWN, model '' for a disk with no serial.
    declared = json.loads(json.dumps(MANIFEST["nodes"]["lumen"]))
    declared["devices"].append({"model": "", "serial": "UNKNOWN", "size": "119.2G"})
    assert smd.diff_node("lumen", declared, live()) == []
    assert "UNKNOWN" in [d["serial"] for d in smd.regen_node(declared, live())["devices"]]


def test_regen_keeps_by_id_mount_identity():
    node = smd.regen_node(lvm_root_node(), with_by_id(live(), [LVM_ID]))
    assert node["mounts"][0]["uuid"] == smd.BYID_DIR + LVM_ID


# ---------------------------------------------------------------------- regen

def test_regen_keeps_hand_fields_and_optional_entries():
    lsblk = {"blockdevices": LSBLK["blockdevices"][:3]}
    node = smd.regen_node(MANIFEST["nodes"]["lumen"], live(lsblk=lsblk))
    assert node["governor"] == "performance"
    assert node["optional_devices"] == ["ZDZRTG6X"]
    assert "ZDZRTG6X" in [d["serial"] for d in node["devices"]]
    root = next(m for m in node["mounts"] if m["target"] == "/")
    assert root["required"] is True and root["noauto"] is False
    assert node["arrays"][0]["uuid"] == "8716cdfb:daba59e6:49d29d2e:d3b16e35"


def test_regen_of_clean_survey_is_identity():
    candidate, findings, coverage = smd.check_manifest(
        MANIFEST, {"lumen": live(), "hathor": {"status": "parked"}})
    assert findings == []
    assert coverage == {"hathor": "parked", "lumen": "ok"}
    assert smd.yaml_diff(MANIFEST, candidate) == ""


def test_unreachable_node_is_coverage_not_drift():
    candidate, findings, coverage = smd.check_manifest(
        MANIFEST, {"lumen": {"status": "unreachable", "error": "timeout"}})
    assert findings == [] and coverage["lumen"] == "unreachable"
    assert candidate["nodes"]["lumen"] == MANIFEST["nodes"]["lumen"]


def test_survey_all_skips_parked_without_ssh(monkeypatch):
    calls = []
    monkeypatch.setattr(smd, "survey_node", lambda n, ip, h: calls.append(n) or live())
    surveys = smd.survey_all(MANIFEST, roster={}, local_host="lumen")
    assert calls == ["lumen"] and surveys["hathor"] == {"status": "parked"}


def test_survey_node_without_address():
    assert smd.survey_node("sobek", None, "lumen") == {"status": "no_address"}


# ------------------------------------------------------------------ FLEET_MAP

def make_db(path, facts):
    conn = sqlite3.connect(path)
    conn.execute("create table state_facts (id TEXT PRIMARY KEY, subject TEXT, "
                 "predicate TEXT, value TEXT, valid_from TEXT, valid_to TEXT)")
    conn.executemany("insert into state_facts values (?,?,?,?,?,?)", facts)
    conn.commit()
    conn.close()


def test_fleet_map_lag_and_missing_ip(tmp_path):
    db = tmp_path / "m.db"
    make_db(db, [
        ("1", "sobek", "tailscale_ip", "100.64.0.9", "2026-09-10T00:00:00+00:00", None),
        ("2", "bes", "tailscale_ip", "100.64.0.7", "2026-09-01T00:00:00+00:00", None),
        ("3", "ghost", "tailscale_ip", "100.64.0.5", "2026-09-01T00:00:00+00:00", None),
        ("4", "sobek", "tailscale_ip", "100.64.0.1", "2026-08-01T00:00:00+00:00",
         "2026-09-10T00:00:00+00:00"),
    ])
    fmap = tmp_path / "FLEET_MAP.md"
    fmap.write_text("| sobek | 100.64.0.1 |\n| bes | 100.64.0.7 |\n")
    ts = smd.parse_ts("2026-09-05T00:00:00+00:00").timestamp()
    os.utime(fmap, (ts, ts))
    findings, info = smd.check_fleet_map(str(fmap), str(db))
    assert info["lag_days"] == 5.0
    assert kinds(findings) == [("fleet_map_stale", "mtime"),
                               ("fleet_map_value_missing", "tailscale_ip")]
    assert [f["node"] for f in findings] == ["FLEET_MAP.md", "sobek"]


def test_fleet_map_fresh_is_clean(tmp_path):
    db = tmp_path / "m.db"
    make_db(db, [("1", "bes", "tailscale_ip", "100.64.0.7", "2026-09-01T00:00:00Z", None)])
    fmap = tmp_path / "FLEET_MAP.md"
    fmap.write_text("bes 100.64.0.7\n")
    findings, _ = smd.check_fleet_map(str(fmap), str(db))
    assert findings == []


def test_annotated_store_value_matches_on_ip_token(tmp_path):
    db = tmp_path / "m.db"
    make_db(db, [
        ("1", "lumen", "lan_ip", "192.168.8.211 (enp12s0, Lumen LAN — supersedes .180)",
         "2026-09-01T00:00:00Z", None),
        ("2", "bes", "lan_ip", "192.168.8.190 (static, nmcli)", "2026-09-01T00:00:00Z", None),
        ("3", "sobek", "lan_ip", "DHCP (no static yet)", "2026-09-01T00:00:00Z", None),
    ])
    fmap = tmp_path / "FLEET_MAP.md"
    fmap.write_text("lumen 192.168.8.211\nbes 192.168.8.19\nsobek\n")
    findings, _ = smd.check_fleet_map(str(fmap), str(db))
    assert [(f["node"], f["live"]) for f in findings] == [("bes", "192.168.8.190")]


def test_ip_prefix_does_not_count_as_carried(tmp_path):
    db = tmp_path / "m.db"
    make_db(db, [("1", "bes", "tailscale_ip", "100.64.0.7", "2026-09-01T00:00:00Z", None)])
    fmap = tmp_path / "FLEET_MAP.md"
    fmap.write_text("bes 100.64.0.71\n")
    findings, _ = smd.check_fleet_map(str(fmap), str(db))
    assert kinds(findings) == [("fleet_map_value_missing", "tailscale_ip")]


# ------------------------------------------------------------- raise + state

@pytest.fixture
def env(tmp_path, monkeypatch):
    manifest = tmp_path / "storage_manifest.yaml"
    manifest.write_text(yaml.safe_dump(MANIFEST))
    before = manifest.read_bytes()
    calls = []
    monkeypatch.setattr(smd, "run", lambda cmd, **kw: calls.append(cmd) or (0, "queued id=1", ""))
    yield {"manifest": manifest, "before": before, "calls": calls,
           "state": tmp_path / "state"}
    assert manifest.read_bytes() == before, "diff-and-report must never edit the manifest"


def drifted():
    lsblk = {"blockdevices": LSBLK["blockdevices"] + [disk("sdb", "NEWSERIAL")]}
    return {"lumen": live(lsblk=lsblk)}


def test_apply_raises_aq_and_wq_once_per_fingerprint(env):
    args = dict(map_path=None, db_path=None, apply=True, state_dir=str(env["state"]))
    r1 = smd.check(str(env["manifest"]), surveys=drifted(), **args)
    assert [c[:2] for c in env["calls"]] == [[smd.AQ, "add"], [smd.WQ, "add"]]
    aq = env["calls"][0]
    assert aq[2:4] == ["storage-manifest-drift", "drift"] and len(aq) == 6
    wq = env["calls"][1]
    assert wq[wq.index("--title") + 1] == smd.WQ_TITLE and "--lane" in wq
    assert "NEWSERIAL" in wq[wq.index("--detail") + 1]

    r2 = smd.check(str(env["manifest"]), surveys=drifted(), **args)
    assert len(env["calls"]) == 2, "same drift set must not re-file"
    assert r2["fingerprint"] == r1["fingerprint"]
    assert r2["actions"]["raised"].startswith("already raised")

    for name in ("report.txt", "storage_manifest.candidate.yaml", "storage_manifest.diff"):
        assert (env["state"] / name).exists()
    added = [line for line in (env["state"] / "storage_manifest.diff").read_text().splitlines()
             if line.startswith("+") and not line.startswith("+++")]
    assert [line.split()[-1] for line in added] == ["SAMSUNG MZFLV256HCHP-000MV".split()[-1],
                                                    "NEWSERIAL", "238.5G"]


def test_clean_run_raises_nothing(env):
    r = smd.check(str(env["manifest"]), None, None, surveys={"lumen": live()}, apply=True,
                  state_dir=str(env["state"]))
    assert env["calls"] == [] and r["actions"] == {"raised": "no drift"}
    assert r["diff_lines"] == 0
    assert (env["state"] / "storage_manifest.diff").read_text() == ""


def test_report_only_run_never_calls_aq_or_wq(env):
    smd.check(str(env["manifest"]), None, None, surveys=drifted(), apply=False,
              state_dir=str(env["state"]))
    assert env["calls"] == []


def test_cli_strict_exit_and_survey_json(env, tmp_path, capsys):
    survey = tmp_path / "survey.json"
    survey.write_text(json.dumps(drifted()))
    rc = smd.main(["--manifest", str(env["manifest"]), "--no-fleet-map",
                   "--survey-json", str(survey), "--state-dir", str(env["state"]),
                   "--strict"])
    assert rc == 1
    assert "device_undeclared" in capsys.readouterr().out
