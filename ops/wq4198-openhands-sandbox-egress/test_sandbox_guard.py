import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import sandbox_guard as g  # noqa: E402


def container(name, ip=None, running=True, memory=0, nano_cpus=0, pids=None):
    nets = {"bridge": {"IPAddress": ip}} if ip else {"openhands_default": {"IPAddress": "172.21.0.2"}}
    return {
        "Name": "/" + name,
        "State": {"Running": running},
        "NetworkSettings": {"Networks": nets},
        "HostConfig": {"Memory": memory, "NanoCpus": nano_cpus, "PidsLimit": pids},
    }


def verdict(rules, src, dst, dport, state="NEW"):
    """Walk OH-SBX-SRC -> OH-SBX-EGRESS for one packet from docker0, using
    the rule strings the guard emits. Returns RETURN or REJECT."""
    chains = {}
    for r in rules:
        chains.setdefault(r.split()[1], []).append(r)

    def walk(chain):
        for r in chains[chain]:
            m = re.search(r"-s (\S+)/32", r)
            if m and m.group(1) != src:
                continue
            m = re.search(r"--ctstate (\S+)", r)
            if m and state not in m.group(1).split(","):
                continue
            m = re.search(r"--ctorigdst (\S+)/32 --ctorigdstport (\d+)", r)
            if m and (m.group(1), int(m.group(2))) != (dst, dport):
                continue
            target = r.split(" -j ")[1].split()[0]
            if target == "LOG":
                continue
            if target in chains:
                return walk(target)
            return target
        return "RETURN"

    return walk(g.SRC_CHAIN)


# The ticket's measured reach from a docker0 container (wq#4198 detail).
# Horus/Ma'at LAN IPs from lumen_state (horus.lan_ip, maat.lan_ip).
MUST_BLOCK = [
    ("172.17.0.1", 22), ("192.168.8.160", 22), ("192.168.8.231", 22),
    ("192.168.8.202", 22), ("1.1.1.1", 443), ("100.83.19.124", 22),
]
MUST_ALLOW = [("172.17.0.1", 4000), ("172.17.0.1", 3000)]


class Classify(unittest.TestCase):
    def test_sandbox_vs_exempt_vs_ignored(self):
        cs = [
            container("oh-agent-server-abc", ip="172.17.0.5"),
            container("searxng", ip="172.17.0.3"),
            container("stopped-thing", ip="172.17.0.9", running=False),
            container("openhands"),  # not on docker0
        ]
        sandboxes, exempt = g.classify(cs)
        self.assertEqual([c["Name"] for c in sandboxes], ["/oh-agent-server-abc"])
        self.assertEqual(exempt, [("172.17.0.3", "searxng")])

    def test_a_sandbox_is_never_exempted(self):
        _, exempt = g.classify([container("oh-agent-server-x", ip="172.17.0.7")])
        self.assertEqual(exempt, [])


class Egress(unittest.TestCase):
    def setUp(self):
        self.rules = g.egress_rules() + g.src_rules([("172.17.0.3", "searxng")])

    def test_sandbox_matrix(self):
        for dst, port in MUST_BLOCK:
            self.assertEqual(verdict(self.rules, "172.17.0.5", dst, port), "REJECT", (dst, port))
        for dst, port in MUST_ALLOW:
            self.assertEqual(verdict(self.rules, "172.17.0.5", dst, port), "RETURN", (dst, port))

    def test_replies_from_sandbox_pass(self):
        # app -> sandbox agent-server port; the sandbox's reply is ESTABLISHED.
        self.assertEqual(verdict(self.rules, "172.17.0.5", "172.21.0.2", 40123, "ESTABLISHED"), "RETURN")

    def test_exempt_container_untouched(self):
        for dst, port in MUST_BLOCK:
            self.assertEqual(verdict(self.rules, "172.17.0.3", dst, port), "RETURN")

    def test_unknown_docker0_source_is_restricted(self):
        # fail-closed: zero exemptions (boot-time start) restricts everyone.
        rules = g.egress_rules() + g.src_rules([])
        self.assertEqual(verdict(rules, "172.17.0.3", "1.1.1.1", 443), "REJECT")

    def test_never_accepts(self):
        self.assertFalse([r for r in self.rules if "-j ACCEPT" in r])

    def test_allowlist_is_exactly_the_ticket_pair(self):
        self.assertEqual({(ip, port) for ip, port, _ in g.ALLOW}, set(MUST_ALLOW))


class Fragment(unittest.TestCase):
    def test_static_fragment_leaves_src_chain_alone(self):
        f = g.restore_fragment(None)
        self.assertNotIn(g.SRC_CHAIN, f)
        self.assertTrue(f.startswith("*filter\n") and f.endswith("COMMIT\n"))

    def test_drift_guard_fixture_matches_emitted_rules(self):
        """wq#2819's fixture must carry the rules this module really emits,
        or its SandboxGuardTest pins a ruleset nobody installs."""
        path = os.path.join(os.path.dirname(__file__), "..", "wq2819-iptables-drift-guard", "fixtures", "wq4198_sandbox_guard.rules")
        with open(path) as f:
            fixture = set(f.read().splitlines())
        emitted = g.egress_rules() + g.src_rules([("172.17.0.3", "searxng")])
        emitted += [f"-A {chain} {' '.join(spec)}" for chain, spec in g.JUMPS]
        self.assertEqual(set(emitted) - fixture, set())

    def test_reconcile_fragment_redeclares_both_chains(self):
        f = g.restore_fragment([("172.17.0.3", "searxng")])
        self.assertIn(f":{g.SRC_CHAIN} - [0:0]", f)
        self.assertIn(f":{g.EGRESS_CHAIN} - [0:0]", f)
        self.assertTrue(f.splitlines()[-2].endswith(f"-j {g.EGRESS_CHAIN}"))


class Limits(unittest.TestCase):
    def test_unlimited_sandbox_needs_limits(self):
        self.assertTrue(g.needs_limits(container("oh-agent-server-a", ip="172.17.0.5")))
        self.assertTrue(g.needs_limits(container("oh-agent-server-a", ip="172.17.0.5", memory=1, nano_cpus=1, pids=-1)))

    def test_limited_sandbox_left_alone(self):
        c = container("oh-agent-server-a", ip="172.17.0.5", memory=6 << 30, nano_cpus=2_000_000_000, pids=2048)
        self.assertFalse(g.needs_limits(c))

    def test_update_argv_sets_swap_with_memory(self):
        argv = g.update_argv("oh-agent-server-a")
        self.assertEqual(argv[:2], ["docker", "update"])
        self.assertEqual(argv[-1], "oh-agent-server-a")
        self.assertIn("--memory-swap", argv)
        self.assertIn("--pids-limit", argv)


if __name__ == "__main__":
    unittest.main()
