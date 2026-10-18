import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import iptables_drift_guard as guard  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _read(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


class DiffLinesTest(unittest.TestCase):
    """fixtures/live_rules.v4 is wq#2808's committed live snapshot (337 lines);
    fixtures/stale_rules.v4 is the same file with exactly the 6 dockerd-owned
    guard rules this ticket names removed (3x `172.x ! -i br-*` DROP, the
    `127.0.0.1:4000`/`:5006` lo-only DROPs, one DOCKER accept) -- i.e. the
    class of persisted-file lag wq#2819 describes, pinned to real fixture
    data instead of a live mutable file."""

    def test_detects_the_dockerd_guard_class(self):
        only_live, only_persisted = guard.diff_lines(
            _read("live_rules.v4"), _read("stale_rules.v4")
        )
        self.assertEqual(only_persisted, [])
        self.assertEqual(
            set(only_live),
            {
                "-A PREROUTING -d 172.18.0.4/32 ! -i br-f037019f74bf -j DROP",
                "-A PREROUTING -d 172.18.0.2/32 ! -i br-f037019f74bf -j DROP",
                "-A PREROUTING -d 172.18.0.3/32 ! -i br-f037019f74bf -j DROP",
                "-A PREROUTING -d 127.0.0.1/32 ! -i lo -p tcp -m tcp --dport 4000 -j DROP",
                "-A PREROUTING -d 127.0.0.1/32 ! -i lo -p tcp -m tcp --dport 5006 -j DROP",
                "-A DOCKER -d 172.25.0.2/32 ! -i br-e1aa348ff221 -o br-e1aa348ff221 -p tcp -m tcp --dport 5006 -j ACCEPT",
            },
        )

    def test_identical_rulesets_have_no_drift(self):
        live = _read("live_rules.v4")
        only_live, only_persisted = guard.diff_lines(live, live)
        self.assertEqual(only_live, [])
        self.assertEqual(only_persisted, [])

    def test_counter_churn_alone_is_not_drift(self):
        """iptables-save re-reads packet/byte counters on every call; two
        dumps of an unchanged ruleset taken seconds apart must not register
        as drift, or every run would false-page."""
        live = _read("live_rules.v4")
        churned = live.replace("[0:0]", "[482:91234]", 1)
        only_live, only_persisted = guard.diff_lines(churned, live)
        self.assertEqual(only_live, [])
        self.assertEqual(only_persisted, [])

    def test_a_persisted_only_rule_is_also_reported(self):
        """The class runs both directions: a rule someone removed live but
        never re-saved would show as persisted-only. The diff must surface
        that too, not just the live-ahead case."""
        only_live, only_persisted = guard.diff_lines(
            _read("stale_rules.v4"), _read("live_rules.v4")
        )
        self.assertEqual(only_live, [])
        self.assertEqual(len(only_persisted), 6)


def _with_sandbox_guard(text, drop=()):
    """live_rules.v4 plus wq#4198's committed guard rules, spliced in before
    the filter table's COMMIT, minus any lines in `drop`."""
    extra = [l for l in _read("wq4198_sandbox_guard.rules").splitlines() if l not in drop]
    head, sep, tail = text.partition("*filter\n")
    body, commit, rest = tail.partition("\nCOMMIT\n")
    return head + sep + body + "\n" + "\n".join(extra) + commit + rest


class SandboxGuardTest(unittest.TestCase):
    """wq#4198: OH-SBX-SRC's per-IP exemptions are rewritten by
    lumen-oh-sandbox-guard on every container start. Their churn is not
    drift; losing any static egress rule is."""

    EXEMPT_3 = "-A OH-SBX-SRC -s 172.17.0.3/32 -m comment --comment wq4198:exempt:searxng -j RETURN"
    REJECT_TCP = "-A OH-SBX-EGRESS -p tcp -m comment --comment wq4198 -j REJECT --reject-with tcp-reset"
    SRC_TAIL = "-A OH-SBX-SRC -m comment --comment wq4198 -j OH-SBX-EGRESS"

    def test_exemption_churn_is_not_drift(self):
        live = _with_sandbox_guard(_read("live_rules.v4"))
        persisted = _with_sandbox_guard(_read("live_rules.v4"), drop={self.EXEMPT_3})
        self.assertEqual(guard.diff_lines(live, persisted), ([], []))

    def test_lost_egress_reject_is_drift(self):
        live = _with_sandbox_guard(_read("live_rules.v4"), drop={self.REJECT_TCP})
        persisted = _with_sandbox_guard(_read("live_rules.v4"))
        self.assertEqual(guard.diff_lines(live, persisted), ([], [self.REJECT_TCP]))

    def test_lost_src_tail_jump_is_drift(self):
        live = _with_sandbox_guard(_read("live_rules.v4"), drop={self.SRC_TAIL})
        persisted = _with_sandbox_guard(_read("live_rules.v4"))
        self.assertEqual(guard.diff_lines(live, persisted), ([], [self.SRC_TAIL]))

    def test_splice_lands_inside_filter_table(self):
        text = _with_sandbox_guard(_read("live_rules.v4"))
        filter_table = text.split("*filter\n", 1)[1].split("\nCOMMIT\n", 1)[0]
        self.assertIn(self.EXEMPT_3, filter_table)


class RunTest(unittest.TestCase):
    """Exercises run()'s orchestration (save-then-reverify, alerting, exit
    codes) against the same fixture pair, with the real iptables-save/
    netfilter-persistent/ntfy-send calls mocked out."""

    def setUp(self):
        self.live = _read("live_rules.v4")
        self.stale = _read("stale_rules.v4")
        patch_state = mock.patch.object(guard, "_write_state")
        patch_state.start()
        self.addCleanup(patch_state.stop)

    def test_no_drift_saves_nothing(self):
        with mock.patch.object(guard, "live_rules", return_value=self.live), \
             mock.patch.object(guard, "persisted_rules", return_value=self.live), \
             mock.patch.object(guard, "save") as m_save, \
             mock.patch.object(guard, "notify") as m_notify:
            rc = guard.run()
        self.assertEqual(rc, 0)
        m_save.assert_not_called()
        m_notify.assert_not_called()

    def test_drift_dry_run_reports_but_does_not_save(self):
        with mock.patch.object(guard, "live_rules", return_value=self.live), \
             mock.patch.object(guard, "persisted_rules", return_value=self.stale), \
             mock.patch.object(guard, "save") as m_save, \
             mock.patch.object(guard, "notify") as m_notify:
            rc = guard.run(dry_run=True)
        self.assertEqual(rc, 3)
        m_save.assert_not_called()
        m_notify.assert_not_called()

    def test_drift_resolved_by_save_notifies_success(self):
        persisted_calls = [self.stale, self.live]  # before save, after save

        def fake_persisted():
            return persisted_calls.pop(0)

        with mock.patch.object(guard, "live_rules", return_value=self.live), \
             mock.patch.object(guard, "persisted_rules", side_effect=fake_persisted), \
             mock.patch.object(guard, "save") as m_save, \
             mock.patch.object(guard, "notify") as m_notify:
            rc = guard.run()
        self.assertEqual(rc, 0)
        m_save.assert_called_once()
        self.assertIn("saved", m_notify.call_args.args[0])

    def test_save_that_does_not_fix_drift_pages_instead_of_claiming_ok(self):
        """If netfilter-persistent save runs but the re-check still shows a
        difference (e.g. dockerd rewrote the rule again mid-run), run() must
        return non-zero and alert -- not report success."""
        with mock.patch.object(guard, "live_rules", return_value=self.live), \
             mock.patch.object(guard, "persisted_rules", return_value=self.stale), \
             mock.patch.object(guard, "save") as m_save, \
             mock.patch.object(guard, "notify") as m_notify:
            rc = guard.run()
        self.assertEqual(rc, 4)
        m_save.assert_called_once()
        self.assertIn("NOT resolved", m_notify.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
