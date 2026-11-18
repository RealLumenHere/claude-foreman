"""wq#5083: Fable never routes RE / exploit / firmware / security tickets."""
import json
import os
import subprocess
import sys

import pytest

ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ORCH_DIR)
import router  # noqa: E402

MANIFEST = os.path.join(ORCH_DIR, "..", "research", "models.yaml")


@pytest.fixture(scope="module")
def m():
    return router.load_manifest(MANIFEST, force=True)


@pytest.fixture(autouse=True)
def cooldown_file(tmp_path, monkeypatch):
    path = tmp_path / "backend_cooldown.json"
    monkeypatch.setattr(router.local_capacity, "COOLDOWN_PATH", str(path))
    monkeypatch.setenv("FOREMAN_BACKEND_COOLDOWN", str(path))
    monkeypatch.setenv("FOREMAN_CLI_CANARY_MODE", "off")
    monkeypatch.setattr(router.cli_canary, "gate", lambda *a, **k: (lambda model: None))


def T(title, detail="", lane="", subsystems=""):
    return {"title": title, "detail": detail, "lane": lane, "subsystems": subsystems}


@pytest.mark.parametrize("ticket", [
    T("[RE] dump the boot partition"),
    T("Miko 3 build v6"),
    T("QuestFree: validate the exploit chain"),
    T("coreswitch firmware bench validation"),
    T("[sec] CRITICAL: anon access on mcp-failover"),
    T("fix the reader", "uses mtkclient to read preloader"),
    T("enter EDL and pull userdata"),
    T("flashrom read of the BIOS chip"),
    T("rename a variable", lane="frank-re"),
    T("tidy", subsystems="build,re"),
    T("design the new protocol shim", "we reverse-engineered the wire format"),
    T("add security headers to the gateway"),
    T("P1 PILOT: Ghidra MCP (bethington, 219 tools)"),
    T("Miko3: USB debugging disables on reboot"),
    T("MikoUnchained: custom ROM feasibility"),
    T("MT8167 AVB green/locked to orange/unlocked"),
    T("[miko3] uart"),
    T("unlock the bootloader on the tablet"),
])
def test_security_tickets_detected(m, ticket):
    assert router.is_security_ticket(ticket, m), ticket


@pytest.mark.parametrize("ticket", [
    T("re-run the nightly probe"),
    T("Regex fix in wq list", "Re: Frank's note"),
    T("design lumen-memoryd new service"),
    T("add conflict guard to lumen-pr"),
    T("secret rotation runbook", lane="fleet"),   # 'sec' is token-only, never a prose prefix
    T("tidy", lane="secrets"),                       # ... and whole-token, not a token prefix
    T("edlin clone for the shell"),                  # 'edl' is whole-word
    T("prefer the reliable path"),                   # 'RE' never matches inside words
])
def test_non_security_tickets_not_flagged(m, ticket):
    assert router.is_security_ticket(ticket, m) == [], ticket


@pytest.mark.parametrize("arch", sorted(set(router.ARCHETYPES) - {"long-horizon-campaign"}))
@pytest.mark.parametrize("fails,cross", [(0, False), (1, False), (2, True), (5, True)])
def test_security_never_selects_fable_any_archetype(m, arch, fails, cross):
    rung, reasons = router.select(m, arch, fails=fails, cross_subsystem=cross, security=True)
    assert rung is not None, reasons
    assert not rung.model.startswith("claude-fable"), (arch, rung, reasons)
    assert rung.model in ("claude-opus-4-6", "claude-opus-5-5"), (arch, rung)


def test_security_primary_is_opus_4_6(m):
    rung, _ = router.select(m, "novel-design", security=True)
    assert (rung.model, rung.effort) == ("claude-opus-4-6", "high")


def test_security_falls_to_opus_5_5_when_4_6_denied(m):
    rung, _ = router.select(m, "novel-design", security=True,
                            pressure=lambda x: "deny" if x == "claude-opus-4-6" else "ok")
    assert (rung.model, rung.effort) == ("claude-opus-5-5", "high")


def test_security_never_takes_fable_even_when_everything_else_denied(m):
    rung, _ = router.select(m, "novel-design", security=True,
                            pressure=lambda x: "ok" if "fable" in x else "deny")
    assert rung is None


def test_campaign_still_decomposes_for_security(m):
    rung, _ = router.select(m, "long-horizon-campaign", security=True)
    assert rung.model == "DECOMPOSE"


def test_non_security_design_still_gets_fable(m):
    rung, _ = router.select(m, "novel-design")
    assert rung.model == "claude-fable-5-2"


def test_orchestrator_row_is_opus_4_6(m):
    rung, _ = router.select(m, "orchestrator")
    assert (rung.model, rung.effort) == ("claude-opus-4-6", "high")


def test_security_guard_swaps_fable_stamp(m):
    g = router.security_guard(T("Miko 3 kernel port design"), "claude-fable-5-2", "high", m)
    assert g and g[0] in ("claude-opus-4-6", "claude-opus-5-5")
    assert "security_exclusion" in g[2]


def test_security_guard_leaves_non_fable_and_non_security_alone(m):
    assert router.security_guard(T("Miko 3 port"), "claude-opus-5-5", "high", m) is None
    assert router.security_guard(T("design memoryd"), "claude-fable-5-2", "high", m) is None


def test_security_guard_fails_closed_on_unreadable_ticket(m, monkeypatch):
    def boom(_):
        raise SystemExit("router: no such ticket 1")
    monkeypatch.setattr(router, "wq_show", boom)
    g = router.security_guard(1, "fable", "high", m)
    assert g and not g[0].startswith("claude-fable")


def test_manifest_check_refuses_fable_in_security_chain(m):
    import copy
    raw = copy.deepcopy(m.raw)
    raw["security_exclusion"]["chain"] = ["fable-5-2@high"]
    with pytest.raises(router.ManifestError, match="never-for-security"):
        router.Manifest(raw)


def test_route_cli_security_flag():
    out = subprocess.run([sys.executable, os.path.join(ORCH_DIR, "router.py"), "--manifest",
                          MANIFEST, "route", "novel-design", "--security"],
                         capture_output=True, text=True,
                         env=dict(os.environ, FOREMAN_CLI_CANARY_MODE="off"))
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["rung"]["model"] == "claude-opus-4-6"
