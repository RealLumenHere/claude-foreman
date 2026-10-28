"""wq#4764 — ops-apply gate + node daemons (design doc §3.2-3.6).

Code + tests only (B2-T1). No install, no live node. The gate runs on Lumen
as an unprivileged-facing socket (root, zero capabilities); the node daemon
runs on the target node behind a forced-command ssh (root, a narrow
CapabilityBoundingSet). Neither trusts the agent: the manifest carries
parameters only, and every apply is revalidated against the catalog, the
pinned policy copy and a one-shot approval (V1-V10, validation.py).
"""
