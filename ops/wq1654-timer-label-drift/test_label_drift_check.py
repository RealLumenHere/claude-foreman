"""wq#1654 tests — fixtures use the exact live Description= strings captured
2026-09-10 07:5x UTC via `systemctl show` (FLEET_SERVICE_ATLAS.md §7 F-3)."""
import label_drift_check as ld

LIVE_OFFENDERS = [
    {"Id": "lumen-familiarity.timer", "ActiveState": "active", "UnitFileState": "enabled",
     "Description": "Entity-familiarity ingest every 6h (installed DISABLED; Frank-gated enable)"},
    {"Id": "lumen-model-audit.timer", "ActiveState": "active", "UnitFileState": "enabled",
     "Description": "External-model correctness audit every 12h (installed DISABLED; Frank-gated enable)"},
    {"Id": "lumen-skillhex.timer", "ActiveState": "active", "UnitFileState": "enabled",
     "Description": "SkillHEX hypothesis cycle 3x/week (installed DISABLED; Frank-gated enable)"},
    {"Id": "lumen-memory-audit.timer", "ActiveState": "active", "UnitFileState": "enabled",
     "Description": "Weekly embedding integrity audit, quiet window (installed DISABLED; Frank-gated enable)"},
    {"Id": "lumen-judgearena-bench.timer", "ActiveState": "active", "UnitFileState": "enabled",
     "Description": "Weekly judge-agreement bench (installed DISABLED; Frank-gated enable)"},
    {"Id": "lumen-pelican-bench.timer", "ActiveState": "active", "UnitFileState": "enabled",
     "Description": "Weekly pelican probe battery (DISABLED by default - Frank-gated enable)"},
    {"Id": "lumen-foreman-dispatch.timer", "ActiveState": "active", "UnitFileState": "enabled",
     "Description": "20-min cadence for lumen-foreman-dispatch (STAGED — Frank enables; wq#715)"},
    {"Id": "lumen-foreman-health.timer", "ActiveState": "active", "UnitFileState": "enabled",
     "Description": "5-min cadence for lumen-foreman-health (STAGED — Frank enables; wq#825)"},
    {"Id": "lumen-foreman-selfheal.timer", "ActiveState": "active", "UnitFileState": "enabled",
     "Description": "10-min cadence for lumen-foreman-selfheal (STAGED — Frank enables; wq#725)"},
]


def test_flags_all_nine_live_offenders():
    bad = {u["Id"] for u in ld.violations(LIVE_OFFENDERS)}
    assert bad == {u["Id"] for u in LIVE_OFFENDERS}


def test_clears_once_label_is_fixed():
    fixed = [dict(u, Description="Entity-familiarity ingest every 6h") for u in LIVE_OFFENDERS[:1]]
    assert ld.violations(fixed) == []


def test_ignores_stale_label_when_actually_inactive():
    # Description says DISABLED and it really is inactive/disabled: no drift.
    honest = [{"Id": "x.timer", "ActiveState": "inactive", "UnitFileState": "disabled",
               "Description": "Something (installed DISABLED; Frank-gated enable)"}]
    assert ld.violations(honest) == []


def test_ignores_clean_active_timer():
    clean = [{"Id": "y.timer", "ActiveState": "active", "UnitFileState": "enabled",
              "Description": "Nightly cleanup"}]
    assert ld.violations(clean) == []


def test_case_insensitive_match():
    u = [{"Id": "z.timer", "ActiveState": "active", "UnitFileState": "enabled",
          "Description": "installed disabled; frank-gated enable"}]
    assert len(ld.violations(u)) == 1
