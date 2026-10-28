"""V1-V10 apply-time revalidation (design §3.4). Every check is a pure
function: it takes already-fetched data (a peer cgroup string, a lease
record, a catalog dict, ...) and either returns normally or raises
OpsApplyError with the check's code. No check does its own I/O — the gate
and node servers (gate_server.py, node_server.py) fetch the inputs and call
these in order, so "the first failure refuses with its code, and nothing is
staged on a refusal" (design) is enforced by ordinary exception propagation,
and the whole matrix is unit-testable without a socket, a wq db or ssh.

THOR_HARDLOCK is a code constant, not derived from policy data, mirroring
/home/lumen/bin/fleet_exec_policy.py's THOR_HARDLOCK (instinct tier 0 #1):
a corrupted or badly-edited policy copy can still never open thor.
"""
import fnmatch
import os
import time

import jsonschema

from .errors import OpsApplyError
from .identity import WORKER_SLICE_MARKER, worker_ticket_from_cgroup
from .canonical import manifest_digest

THOR_HARDLOCK = frozenset({"thor"})

EXPECTED_LANE = "ops-apply"  # wq#4688 lane profile


# ---------------------------------------------------------------------------
# V1 identity
# ---------------------------------------------------------------------------

def check_v1_identity(peer_cgroup_text, manifest_ticket):
    """SO_PEERCRED pid -> cgroup must sit under
    lumen-workers.slice/lumen-job-fworker-wq<N>-*.service, and N must equal
    manifest['ticket']."""
    ticket = worker_ticket_from_cgroup(peer_cgroup_text)
    if ticket is None:
        raise OpsApplyError("OPS_IDENTITY",
                             "peer cgroup is not a lumen-job-fworker-wqN unit")
    if ticket != int(manifest_ticket):
        raise OpsApplyError(
            "OPS_IDENTITY",
            f"cgroup ticket {ticket} != manifest ticket {manifest_ticket}")
    return ticket


# ---------------------------------------------------------------------------
# V2 lease
# ---------------------------------------------------------------------------

def check_v2_lease(lease_record, ticket, unit_name, *, now=None):
    """wq: ticket is claimed, in the ops-apply lane, lease unexpired, holder
    is the calling unit."""
    now = time.time() if now is None else now
    if lease_record is None:
        raise OpsApplyError("OPS_LEASE", f"ticket {ticket} has no lease record")
    if lease_record.get("status") != "claimed":
        raise OpsApplyError(
            "OPS_LEASE",
            f"ticket {ticket} status={lease_record.get('status')!r}, not claimed")
    if lease_record.get("lane") != EXPECTED_LANE:
        raise OpsApplyError(
            "OPS_LEASE",
            f"ticket {ticket} lane={lease_record.get('lane')!r}, not {EXPECTED_LANE!r}")
    expires_at = lease_record.get("lease_expires_at")
    if expires_at is None or expires_at <= now:
        raise OpsApplyError("OPS_LEASE", f"ticket {ticket} lease expired")
    if lease_record.get("lease_owner") != unit_name:
        raise OpsApplyError(
            "OPS_LEASE",
            f"ticket {ticket} lease_owner={lease_record.get('lease_owner')!r} "
            f"!= caller {unit_name!r}")


# ---------------------------------------------------------------------------
# V3 host
# ---------------------------------------------------------------------------

def check_v3_host(manifest_node, *, pinned_hostname, pinned_machine_id,
                   reported_machine_id, never_nodes):
    """manifest.node must equal this node's pinned hostname AND machine-id.
    Node must not be in never.nodes or THOR_HARDLOCK, re-asserted here
    whatever a policy copy says."""
    never = set(never_nodes) | THOR_HARDLOCK
    if manifest_node in never:
        raise OpsApplyError("OPS_HOST", f"{manifest_node} is in never.nodes")
    if manifest_node != pinned_hostname:
        raise OpsApplyError(
            "OPS_HOST",
            f"manifest node {manifest_node!r} != pinned hostname {pinned_hostname!r}")
    if reported_machine_id != pinned_machine_id:
        raise OpsApplyError("OPS_HOST", "machine-id mismatch for pinned node")


# ---------------------------------------------------------------------------
# V4 catalog
# ---------------------------------------------------------------------------

def find_catalog_entry(catalog, recipe_id, recipe_version):
    for entry in catalog.get("recipes", []):
        if entry["id"] == recipe_id and entry["version"] == int(recipe_version):
            return entry
    return None


def check_v4_catalog(catalog, manifest):
    """recipe id+version is in catalog.json for this node, and not frozen on
    it."""
    entry = find_catalog_entry(catalog, manifest["recipe"], manifest["recipe_version"])
    if entry is None:
        raise OpsApplyError(
            "OPS_NOT_ALLOWED",
            f"{manifest['recipe']}.v{manifest['recipe_version']} not in catalog")
    node = manifest["node"]
    if node not in entry.get("nodes", []):
        raise OpsApplyError("OPS_NOT_ALLOWED", f"{entry['id']} not enabled for node {node}")
    if node in entry.get("frozen_nodes", []):
        raise OpsApplyError("OPS_NOT_ALLOWED", f"{entry['id']} is frozen on node {node}")
    return entry


# ---------------------------------------------------------------------------
# V5 param schema
# ---------------------------------------------------------------------------

def check_v5_params(entry, params):
    """JSON Schema validation, additionalProperties: false. The schema is
    the catalog's source of truth; we additionally refuse a schema that
    forgot additionalProperties: false rather than silently trusting it."""
    schema = entry["param_schema"]
    if schema.get("additionalProperties") is not False:
        raise OpsApplyError("OPS_PARAMS", "catalog param_schema missing additionalProperties:false")
    try:
        jsonschema.validate(params, schema)
    except jsonschema.ValidationError as e:
        raise OpsApplyError("OPS_PARAMS", str(e)) from e


# ---------------------------------------------------------------------------
# V6 policy
# ---------------------------------------------------------------------------

def check_v6_policy(policy, *, policy_sha_actual, catalog_policy_sha, unit, node):
    """Root-owned pinned policy copy, sha-pinned by catalog.json. The
    recipe's unit must not match never.restart[node]."""
    if policy is None:
        raise OpsApplyError("OPS_POLICY", "policy_unreadable")
    if policy_sha_actual != catalog_policy_sha:
        raise OpsApplyError("OPS_POLICY", "pinned policy copy sha mismatch")
    globs = policy.get("never", {}).get("restart", {}).get(node, [])
    for g in globs:
        if fnmatch.fnmatchcase(unit, g):
            raise OpsApplyError("OPS_POLICY", f"{unit} matches never.restart[{node}]={g!r}")


# ---------------------------------------------------------------------------
# V7 approval
# ---------------------------------------------------------------------------

def check_not_self_approval(approver_cgroup_text):
    """The narrower half of V7 usable before an approval record exists at
    all: at `ops-apply approve`'s very first call the request is still
    STAGED (no expires_at/consumed_at yet), so the self-approval guard has
    to stand on its own rather than inside check_v7_approval, which
    assumes a fully-formed approval record."""
    if approver_cgroup_text is not None and WORKER_SLICE_MARKER in approver_cgroup_text:
        raise OpsApplyError("OPS_APPROVAL", "approver is a worker (self-approval)")


def check_v7_approval(approval_record, digest, *, approver_cgroup_text=None, now=None):
    """A one-shot approval for this digest, unexpired, unconsumed. The
    approver's peer cgroup must not be under lumen-workers.slice (a worker
    cannot approve itself or another worker). Does not consume the approval
    — that is an atomic I/O step the caller (queue.py) does after this
    passes, re-checking consumed_at under a lock."""
    now = time.time() if now is None else now
    if approval_record is None:
        raise OpsApplyError("OPS_APPROVAL", "no approval for this digest")
    if approval_record.get("digest") != digest:
        raise OpsApplyError("OPS_APPROVAL", "approval digest mismatch")
    if approval_record.get("consumed_at") is not None:
        raise OpsApplyError("OPS_APPROVAL", "approval already consumed")
    expires_at = approval_record.get("expires_at")
    if expires_at is None or expires_at <= now:
        raise OpsApplyError("OPS_APPROVAL", "approval expired")
    check_not_self_approval(approver_cgroup_text)


# ---------------------------------------------------------------------------
# V8 signature
# ---------------------------------------------------------------------------

def check_v8_signature_gate(entry, recipe_file_sha256_actual):
    """Gate half: the recipe file on disk still hashes to the catalog's
    pin."""
    if recipe_file_sha256_actual != entry["sha256"]:
        raise OpsApplyError("OPS_SIGNATURE", "recipe file sha256 != catalog pin")


def check_v8_signature_node(verify_fn, *, digest, signature, allowed_signers_path):
    """Node half: ssh-keygen -Y verify of the gate's signature over the
    digest, against /etc/ops-apply/allowed_signers. `verify_fn` wraps the
    subprocess call (signing.verify_digest) so this stays unit-testable."""
    if not verify_fn(digest=digest, signature=signature,
                      allowed_signers_path=allowed_signers_path):
        raise OpsApplyError("OPS_SIGNATURE", "ssh-keygen -Y verify failed")


# ---------------------------------------------------------------------------
# V9 argv deny
# ---------------------------------------------------------------------------

def _argv_deny_hit(argv, deny_patterns):
    if not argv:
        return None
    base = os.path.basename(argv[0])
    joined = " ".join(argv)
    for pattern in deny_patterns:
        target = joined if " " in pattern else base
        if fnmatch.fnmatchcase(target, pattern):
            return pattern
    return None


def check_v9_argv(rendered_argvs, policy):
    """Every argv the recipe will exec, checked against
    never.sudo_argv_deny. Called once before staging (on the recipe's full
    preview) and again immediately before each exec (node_server re-renders
    and re-checks at apply time, not just at validation time)."""
    deny = policy.get("never", {}).get("sudo_argv_deny", [])
    for argv in rendered_argvs:
        hit = _argv_deny_hit(argv, deny)
        if hit:
            raise OpsApplyError("OPS_ARGV_DENY", f"{argv!r} matches deny pattern {hit!r}")


# ---------------------------------------------------------------------------
# V10 digest
# ---------------------------------------------------------------------------

def check_v10_digest(manifest, *, approved_digest=None):
    """Recompute the canonical-JSON sha256; it must equal manifest['digest'],
    which (when an approval is in play) must equal the approved digest."""
    actual = manifest_digest(manifest)
    if actual != manifest.get("digest"):
        raise OpsApplyError("OPS_DIGEST", "recomputed digest != manifest.digest")
    if approved_digest is not None and actual != approved_digest:
        raise OpsApplyError("OPS_DIGEST", "digest != approved digest")
    return actual


# ---------------------------------------------------------------------------
# recipe-contract TOCTOU recheck (§3.3 step 6) — shares the refusal shape
# but is not one of the V1-V10 apply-time revalidation codes
# ---------------------------------------------------------------------------

def check_toctou(live_sha_now, live_sha_at_read):
    """Step 6 of the recipe contract (§3.3): the live file must not have
    changed between the read and the rename. Not part of V1-V10's apply-time
    revalidation table, but shares the fail-closed refusal shape."""
    if live_sha_now != live_sha_at_read:
        raise OpsApplyError("OPS_TOCTOU", "live file changed since it was read")
