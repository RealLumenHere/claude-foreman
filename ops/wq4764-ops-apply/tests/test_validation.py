"""Each V-code refuses once with a fixture that makes exactly that check
fail while everything before it in the order passes (ticket wq#4764's own
acceptance line)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_apply import validation
from ops_apply.errors import OpsApplyError
from ops_apply.canonical import manifest_digest

WORKER_CGROUP = "0::/lumen-workers.slice/lumen-job-fworker-wq4764-111.service\n"
APPROVER_CGROUP = "0::/user.slice/orchestrator.scope\n"

PARAM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "hostname", "port"],
    "properties": {
        "action": {"enum": ["upsert", "remove"]},
        "hostname": {"type": "string", "pattern": r"^[a-z0-9.-]+\.lumennode\.uk$"},
        "port": {"type": "integer", "minimum": 1024, "maximum": 65535},
        "path": {"type": "string"},
    },
}

CATALOG = {
    "policy_sha256": "deadbeef" * 8,
    "recipes": [
        {"id": "cloudflared.ingress.upsert", "version": 1, "sha256": "f00d" * 16,
         "nodes": ["bes"], "unit": "cloudflared", "frozen_nodes": [],
         "param_schema": PARAM_SCHEMA},
    ],
}

POLICY = {"never": {"nodes": [], "restart": {"bes": []}, "sudo_argv_deny": ["dd", "tailscale up*"]}}


def good_manifest(**overrides):
    m = {
        "recipe": "cloudflared.ingress.upsert", "recipe_version": 1, "node": "bes",
        "ticket": 4764, "params": {"action": "upsert", "hostname": "x.lumennode.uk", "port": 8123},
        "reason": "test",
    }
    m.update(overrides)
    m["digest"] = manifest_digest(m)
    return m


class V1IdentityTests(unittest.TestCase):
    def test_passes_for_matching_worker_and_ticket(self):
        validation.check_v1_identity(WORKER_CGROUP, 4764)

    def test_refuses_non_worker_cgroup(self):
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v1_identity(APPROVER_CGROUP, 4764)
        self.assertEqual(ctx.exception.code, "OPS_IDENTITY")

    def test_refuses_ticket_mismatch(self):
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v1_identity(WORKER_CGROUP, 9999)
        self.assertEqual(ctx.exception.code, "OPS_IDENTITY")


class V2LeaseTests(unittest.TestCase):
    GOOD = {"status": "claimed", "lane": "ops-apply",
            "lease_owner": "lumen-job-fworker-wq4764-111.service", "lease_expires_at": 2000}

    def test_passes_for_matching_lease(self):
        validation.check_v2_lease(self.GOOD, 4764, self.GOOD["lease_owner"], now=1000)

    def test_refuses_missing_lease(self):
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v2_lease(None, 4764, "unit", now=1000)
        self.assertEqual(ctx.exception.code, "OPS_LEASE")

    def test_refuses_wrong_lane(self):
        rec = dict(self.GOOD, lane="build")
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v2_lease(rec, 4764, rec["lease_owner"], now=1000)
        self.assertEqual(ctx.exception.code, "OPS_LEASE")

    def test_refuses_expired_lease(self):
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v2_lease(self.GOOD, 4764, self.GOOD["lease_owner"], now=5000)
        self.assertEqual(ctx.exception.code, "OPS_LEASE")

    def test_refuses_holder_mismatch(self):
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v2_lease(self.GOOD, 4764, "someone-else.service", now=1000)
        self.assertEqual(ctx.exception.code, "OPS_LEASE")


class V3HostTests(unittest.TestCase):
    def test_passes_for_pinned_node(self):
        validation.check_v3_host("bes", pinned_hostname="bes", pinned_machine_id="m1",
                                  reported_machine_id="m1", never_nodes=[])

    def test_refuses_never_nodes(self):
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v3_host("maat", pinned_hostname="bes", pinned_machine_id="m1",
                                      reported_machine_id="m1", never_nodes=["maat"])
        self.assertEqual(ctx.exception.code, "OPS_HOST")

    def test_refuses_thor_hardlock_even_if_policy_omits_it(self):
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v3_host("thor", pinned_hostname="bes", pinned_machine_id="m1",
                                      reported_machine_id="m1", never_nodes=[])
        self.assertEqual(ctx.exception.code, "OPS_HOST")

    def test_refuses_hostname_mismatch(self):
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v3_host("anubis", pinned_hostname="bes", pinned_machine_id="m1",
                                      reported_machine_id="m1", never_nodes=[])
        self.assertEqual(ctx.exception.code, "OPS_HOST")

    def test_refuses_machine_id_mismatch(self):
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v3_host("bes", pinned_hostname="bes", pinned_machine_id="m1",
                                      reported_machine_id="m-impostor", never_nodes=[])
        self.assertEqual(ctx.exception.code, "OPS_HOST")


class V4CatalogTests(unittest.TestCase):
    def test_passes_for_known_recipe_node(self):
        entry = validation.check_v4_catalog(CATALOG, good_manifest())
        self.assertEqual(entry["id"], "cloudflared.ingress.upsert")

    def test_refuses_unknown_recipe(self):
        m = good_manifest(recipe="nonexistent.recipe")
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v4_catalog(CATALOG, m)
        self.assertEqual(ctx.exception.code, "OPS_NOT_ALLOWED")

    def test_refuses_node_not_enabled(self):
        m = good_manifest(node="sobek")
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v4_catalog(CATALOG, m)
        self.assertEqual(ctx.exception.code, "OPS_NOT_ALLOWED")

    def test_refuses_frozen_node(self):
        catalog = {"recipes": [dict(CATALOG["recipes"][0], frozen_nodes=["bes"])]}
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v4_catalog(catalog, good_manifest())
        self.assertEqual(ctx.exception.code, "OPS_NOT_ALLOWED")


class V5ParamsTests(unittest.TestCase):
    def test_passes_for_valid_params(self):
        entry = CATALOG["recipes"][0]
        validation.check_v5_params(entry, {"action": "upsert", "hostname": "x.lumennode.uk", "port": 8123})

    def test_refuses_additional_property(self):
        entry = CATALOG["recipes"][0]
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v5_params(
                entry, {"action": "upsert", "hostname": "x.lumennode.uk", "port": 8123,
                        "url": "http://evil"})
        self.assertEqual(ctx.exception.code, "OPS_PARAMS")

    def test_refuses_schema_without_additional_properties_false(self):
        entry = {"param_schema": {"type": "object"}}
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v5_params(entry, {"action": "upsert"})
        self.assertEqual(ctx.exception.code, "OPS_PARAMS")

    def test_refuses_port_out_of_range(self):
        entry = CATALOG["recipes"][0]
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v5_params(entry, {"action": "upsert", "hostname": "x.lumennode.uk", "port": 22})
        self.assertEqual(ctx.exception.code, "OPS_PARAMS")

    def test_refuses_hostname_outside_zone(self):
        entry = CATALOG["recipes"][0]
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v5_params(entry, {"action": "upsert", "hostname": "x.evil.tld", "port": 8123})
        self.assertEqual(ctx.exception.code, "OPS_PARAMS")


class V6PolicyTests(unittest.TestCase):
    def test_passes_when_unit_not_in_never_restart(self):
        validation.check_v6_policy(POLICY, policy_sha_actual="s", catalog_policy_sha="s",
                                   unit="cloudflared", node="bes")

    def test_refuses_unreadable_policy(self):
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v6_policy(None, policy_sha_actual="s", catalog_policy_sha="s",
                                       unit="cloudflared", node="bes")
        self.assertEqual(ctx.exception.code, "OPS_POLICY")

    def test_refuses_sha_mismatch(self):
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v6_policy(POLICY, policy_sha_actual="tampered", catalog_policy_sha="s",
                                       unit="cloudflared", node="bes")
        self.assertEqual(ctx.exception.code, "OPS_POLICY")

    def test_refuses_never_restart_unit(self):
        policy = {"never": {"restart": {"lumen": ["cloudflared"]}}}
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v6_policy(policy, policy_sha_actual="s", catalog_policy_sha="s",
                                       unit="cloudflared", node="lumen")
        self.assertEqual(ctx.exception.code, "OPS_POLICY")


class V7ApprovalTests(unittest.TestCase):
    GOOD = {"digest": "sha256:abc", "expires_at": 2000, "consumed_at": None}

    def test_passes_for_unexpired_unconsumed_matching_digest(self):
        validation.check_v7_approval(self.GOOD, "sha256:abc",
                                      approver_cgroup_text=APPROVER_CGROUP, now=1000)

    def test_refuses_missing_approval(self):
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v7_approval(None, "sha256:abc", now=1000)
        self.assertEqual(ctx.exception.code, "OPS_APPROVAL")

    def test_refuses_digest_mismatch(self):
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v7_approval(self.GOOD, "sha256:different", now=1000)
        self.assertEqual(ctx.exception.code, "OPS_APPROVAL")

    def test_refuses_already_consumed(self):
        rec = dict(self.GOOD, consumed_at=999)
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v7_approval(rec, "sha256:abc", now=1000)
        self.assertEqual(ctx.exception.code, "OPS_APPROVAL")

    def test_refuses_expired(self):
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v7_approval(self.GOOD, "sha256:abc", now=5000)
        self.assertEqual(ctx.exception.code, "OPS_APPROVAL")

    def test_refuses_self_approval_by_worker_cgroup(self):
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v7_approval(self.GOOD, "sha256:abc",
                                          approver_cgroup_text=WORKER_CGROUP, now=1000)
        self.assertEqual(ctx.exception.code, "OPS_APPROVAL")


class V8SignatureTests(unittest.TestCase):
    def test_gate_passes_for_matching_sha(self):
        entry = {"sha256": "abc123"}
        validation.check_v8_signature_gate(entry, "abc123")

    def test_gate_refuses_sha_mismatch(self):
        entry = {"sha256": "abc123"}
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v8_signature_gate(entry, "tampered")
        self.assertEqual(ctx.exception.code, "OPS_SIGNATURE")

    def test_node_passes_when_verify_fn_true(self):
        validation.check_v8_signature_node(lambda **kw: True, digest="d", signature="s",
                                           allowed_signers_path="/etc/ops-apply/allowed_signers")

    def test_node_refuses_when_verify_fn_false(self):
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v8_signature_node(lambda **kw: False, digest="d", signature="s",
                                               allowed_signers_path="/etc/ops-apply/allowed_signers")
        self.assertEqual(ctx.exception.code, "OPS_SIGNATURE")


class V9ArgvDenyTests(unittest.TestCase):
    def test_passes_for_clean_argv(self):
        validation.check_v9_argv([["cloudflared", "tunnel", "ingress", "validate"]], POLICY)

    def test_refuses_denied_basename(self):
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v9_argv([["dd", "if=/dev/zero"]], POLICY)
        self.assertEqual(ctx.exception.code, "OPS_ARGV_DENY")

    def test_refuses_denied_two_token_pattern(self):
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v9_argv([["tailscale", "up", "--reset"]], POLICY)
        self.assertEqual(ctx.exception.code, "OPS_ARGV_DENY")


class V10DigestTests(unittest.TestCase):
    def test_passes_for_correct_digest(self):
        m = good_manifest()
        self.assertEqual(validation.check_v10_digest(m), m["digest"])

    def test_refuses_tampered_digest(self):
        m = good_manifest()
        m["params"]["port"] = 9999  # tamper after digest was computed
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v10_digest(m)
        self.assertEqual(ctx.exception.code, "OPS_DIGEST")

    def test_refuses_digest_not_matching_approved(self):
        m = good_manifest()
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_v10_digest(m, approved_digest="sha256:" + "0" * 64)
        self.assertEqual(ctx.exception.code, "OPS_DIGEST")


class ToctouTests(unittest.TestCase):
    def test_passes_when_unchanged(self):
        validation.check_toctou("sha-a", "sha-a")

    def test_refuses_when_changed(self):
        with self.assertRaises(OpsApplyError) as ctx:
            validation.check_toctou("sha-b", "sha-a")
        self.assertEqual(ctx.exception.code, "OPS_TOCTOU")


if __name__ == "__main__":
    unittest.main()
