"""wq#2868 judgment regression: the invariant wq#2848's floor-fallback actually buys.

wq#2803 CONTRADICTED wq#2720 because a foreign-prefix stop could cut a body run below
the rule's own floor, failing the whole match and leaving a real token in the clear with
no alarm. wq#2848's fix is not "stop less often" — it is the structural property that a
foreign shape cannot be considered until the rule's own floor is already satisfied, so a
stop can only SHORTEN a match, never prevent one.

This test pins that property rather than a list of offsets: test_mask_foreign_prefix_matrix
samples nine offsets per rule, and sampled offsets are what let wq#2720 ship. The probe
this file drives sweeps every offset from 0 to floor+6 for every rule x every foreign
prefix that rule's charset can spell, and adds three shapes the matrix does not reach —
kv-secret as host (the rule wq#2720 deliberately gave no foreign stop), url-auth as host
(no bounded body at all), and a four-credential chain (whether the multi-pass sweep
drained a chain of stops rather than one). wq#2913 removed the stops and the passes
both; the shapes stay, because what they measure is coverage, not the mechanism.

Section B is the same under-floor class through the OTHER guard `_body()` carries, the
unconditional mask-literal lookahead, which wq#2848 correctly left alone. wq#2922 closes
it with a fail-loud backstop (masking._orphan_matches): the value is not masked (masking
one this short risks re-swallowing `<prefix>MASKED` and reviving the wq#2638 leak), but a
hit now fires every time, so the section's bar is "no SILENT case", not "no fill char
survives" — see the probe's own section_b docstring.

The probe is the evidence artefact of reports/wq2868_judgment_wq2848_floor_fallback.md and
takes a masking-directory argument, so the same cases run against a historical rev.

wq#3018 landed that fix on `main` after wq#3007 found it had only ever existed on an
unmerged branch, and adds what would have caught the landing gap and the two risks the
fix carries: reports/wq3018_probe_orphan_backstop_on_main.py measures the class against
the masking.py actually on the import path (not one extracted from a branch), sweeps
real rev-pinned committed text for the over-firing a fail-loud backstop risks, and runs
the same class through the daemon's hand copy — plus two drift guards pinning
_ORPHAN_SHAPES to the PATTERNS entries it backs and to that daemon copy.
"""
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "..", "reports"))
import wq2868_probe_underfloor_class as probe  # noqa: E402
import wq3018_probe_orphan_backstop_on_main as onmain  # noqa: E402
# The probe puts harness/ on the path for its own `import masking`; reuse it.
import masking  # noqa: E402

sys.path.insert(0, os.path.join(HERE, "..", "..", "token-engine"))
import fworker  # noqa: E402,F401  (side effect: puts the host's fleet_exec_policy on sys.path, as test_mask_foreign_prefix_matrix does)
import token_engine_daemon as ted  # noqa: E402


def _run(fn):
    name, total, fails = fn()
    assert total, f"{name}: probe generated no cases — it cannot fail (wq#2630)"
    return name, total, fails


def test_a_foreign_shape_can_never_prevent_a_match_at_any_offset():
    name, total, fails = _run(probe.section_a)
    assert fails == [], f"{name}: {len(fails)}/{total} tokens not masked at all: {fails[:6]}"


def test_kv_secret_host_is_unaffected_by_foreign_shapes_in_its_value():
    name, total, fails = _run(probe.section_c)
    assert fails == [], f"{name}: {len(fails)}/{total} leaked: {fails[:6]}"


def test_url_auth_host_is_unaffected_by_foreign_shapes_in_its_password():
    name, total, fails = _run(probe.section_d)
    assert fails == [], f"{name}: {len(fails)}/{total} leaked: {fails[:6]}"


def test_a_four_credential_chain_is_drained_and_every_label_fires():
    name, total, fails = _run(probe.section_e)
    assert fails == [], f"{name}: {fails}"


def test_a_body_holding_the_mask_literal_below_the_floor_alarms_instead_of_leaking():
    name, total, fails = _run(probe.section_b)
    assert fails == [], f"{name}: {len(fails)}/{total} SILENT: {fails[:6]}"


def test_the_backstop_stays_quiet_on_real_committed_text():
    """wq#3018: a fail-LOUD backstop fails the other way — alarming on text holding
    no credential. Section B cannot catch that: all 96 of its cases are built to
    trip. This sweeps real committed files instead (rev-pinned, so the run repeats),
    including the mask module and its own tests, which write `<prefix>MASKED` by the
    hundred — the residue wq#2638 taught this guard to ignore."""
    name, total, fails = _run(onmain.quiet)
    assert fails == [], f"{name}: {len(fails)}/{total} files alarmed: {fails[:6]}"


def test_the_daemon_mirror_closes_the_same_under_floor_class():
    """Section B through token_engine_daemon.mask_hits(). A fix that lands in one
    copy only is wq#3007's landing gap one layer down — and here a miss does not
    just go unrecorded, it hands an unmasked broker result back."""
    name, total, fails = _run(onmain.mirrored)
    assert fails == [], f"{name}: {len(fails)}/{total} SILENT: {fails[:6]}"


def test_the_orphan_table_has_not_drifted_from_the_rules_it_backs():
    """wq#3018: `_ORPHAN_SHAPES` names each rule's charset and floor a SECOND time
    rather than deriving them from PATTERNS, which is the drift the now-removed
    `_SHAPES_BY_LABEL` table taught us to fear — raise a floor in PATTERNS alone and
    the backstop silently stops covering the top of that rule's under-floor range.
    Nothing else catches it: section B builds its cases from the probe's own RULES
    table, so a floor that drifts in BOTH masking tables at once still reads green
    there. Pin the two to each other by the body sub-expression they must share."""
    by_label = {}
    for label, rx, _repl in masking.PATTERNS:
        by_label.setdefault(label, []).append(rx.pattern)
    for label, prefix, charset, floor in masking._ORPHAN_SHAPES:
        assert label in by_label, f"orphan shape {label} backs no rule in PATTERNS"
        # aws is the one fixed-width rule ({16}, not {16,}); accept either spelling.
        bodies = (masking._body(charset, "{" + str(floor) + ",}"),
                  masking._body(charset, "{" + str(floor) + "}"))
        owners = [p for p in by_label[label] if prefix in p]
        assert owners, f"orphan shape {label}/{prefix!r} matches no PATTERNS entry"
        assert any(b in p for p in owners for b in bodies), (
            f"orphan shape {label} carries charset {charset} floor {floor}, but no "
            f"PATTERNS entry with that prefix spells that body — the table drifted")


def test_the_daemon_orphan_mirror_has_not_drifted_from_maskings():
    """The daemon runs as `tokeneng`, off this import path, so its backstop is a
    hand copy (same reason test_masking.test_token_engine_mirror_agrees exists).
    Drift here is silent in the direction that misses: a floor or charset only the
    harness copy got means the daemon's outbound tripwire stops failing the broker
    verb on exactly the class wq#2922 was filed to close."""
    mine = [(prefix, charset, floor)
            for _label, prefix, charset, floor in masking._ORPHAN_SHAPES]
    theirs = [(prefix, charset, floor)
              for _name, prefix, charset, floor in ted._ORPHAN_SHAPES]
    assert theirs == mine, "daemon _ORPHAN_SHAPES drifted from masking's"
