# wq#3063: the six values mask_replay.py 3036 reported BURNED?, pinned as committed text.
# None is a credential: every body is lowercase dictionary words joined by `_`, no digit,
# no uppercase. wq#3036 (seed a committed false-positive fixture for test_burn_filter's
# tree-wide sweep) typed a `python3 -c` probe list feeding masking.mask_text() one
# `<name> = <phrase>` sample per kv-secret name (token, auth_token, secret,
# signing_secret, api_secret), and the command text reached the transcript. The sixth,
# after `pending_rotation_secret =`, is the same run's uncommitted fixture line, read
# back through its git-state snapshot. Named by hash, in-process, from the replay's
# verdict seam; wq#3063 printed none of them.
# Leak path: a mask probe has to show the masker what it feeds it; the worker-prompt
# rule (build the body at runtime, never type it after a TOKEN/KEY/SECRET name) is the
# control, nothing to rotate.
# (sha, label, value). Split every six characters so reading this file fires no rule;
# mask_replay closes same-line seams before it searches.
WQ3036_VALUES = (
    ("bf43137fb255", "kv-secret", "queued" "_epoch" "_signi" "ng_mat" "erial"),
    ("9fa83f4bd042", "kv-secret", "rotate" "_epoch" "_signi" "ng_mat" "erial"),
    ("be31c6a7db60", "kv-secret", "pendin" "g_work" "er_cla" "im_val" "ue"),
    ("55ba099e29b2", "kv-secret", "pendin" "g_rota" "tion_m" "ateria" "l_valu" "e"),
    ("14ac1d284ecc", "kv-secret", "pendin" "g_rota" "tion_m" "ateria" "l"),
    ("c30198468832", "kv-secret", "queued" "_hands" "hake_m" "ateria" "l_valu" "e"),
)

WQ3036_RECORDS = frozenset(v[0] for v in WQ3036_VALUES)
