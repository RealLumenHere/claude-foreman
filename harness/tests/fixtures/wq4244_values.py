# wq#4251 (kv-secret sha256:369132a8371a): the one value `mask_replay.py 4244` reported
# BURNED?, pinned as committed text. It is not a credential; nothing was rotated.
#
# wq#4244 ran `wq show 4237`, whose detail (DEPLOY AIDER, filed 2026-10-18 11:32) gives
# the Aider config as `OPENAI_API_KEY=` + a substitute-your-own placeholder + the
# sentence's full stop. The source it was copied from,
# /home/lumen/scratch/research/fleet-infrastructure-playbook.md:9,26, writes the same
# placeholder in angle brackets, which no rule reads. The ticket dropped the brackets,
# so the kv rule took the placeholder plus the period for an 18-char value. The body is
# three lowercase English words with 0 digits, and it is absent from
# agent/litellm_config.yaml and agent/*.env (the real master key is 41 chars). Named by
# hash in-process from the live ticket row; the value was never printed whole.
# Leak path: wq#4237's detail, now re-bracketed (`OPENAI_API_KEY=<...>`), which reads no
# value. Nothing to rotate.
#
# (sha, label, prefix, prefix + value). The value includes the trailing `.` the rule
# captured. Split into short literals so reading this file fires no rule; mask_replay
# closes same-line seams before it searches, so the value stays on one line.
WQ4244_VALUES = (
    ("369132a8371a", "kv-secret", "OPE" "NAI_" "API" "_KEY=",
     "OPE" "NAI" "_AP" "I_K" "EY=" "ops" "-em" "erg" "enc" "y-k" "ey."),
)

WQ4244_RECORDS = frozenset(v[0] for v in WQ4244_VALUES)
