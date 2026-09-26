"""wq#2764: the url-auth values `mask_replay.py 2760` reported BURNED?, named by hash.

wq#2760 fixed bin/mask-secrets and masking.py for DSN passwords, and on the way it
piped hand-typed DSNs through printf/sed in Bash to probe the new rule. Every one of
the four values the replay could not account for is one of those probe dummies, typed
into the command by the model itself: none came from an env, a config or a file.
The row's own value, sha256 0a3d9929c59b, was already committed text: `_DSN_PW` at
ops/wq2316-mask-secrets-boundary/test_mask_secrets_boundary.py. Nothing was rotated.

Each fixture is split after the rule's `scheme://user:` prefix, so this file never
matches url-auth on a read (the _DSN_PW convention). mask_replay's seam-closed view
rejoins the halves, which is what accounts for the two bodies shorter than
TRUNCATED_FLOOR: those only account with their prefix attached.

Exit 0 when every fixture hashes to its target and this file masks to itself.
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                                "harness"))
import masking  # noqa: E402

# (sha256 first 12 hex, prefix + body as the wq#2760 transcript carried it)
NAMED = [
    # `printf 'a=postgresql://u:<v>@db-host…' "'"`: a `%s` that printf fills with a quote
    ("17f27c50b603", "postgresql://u:" "p@ss%sword1"),
    ("9c56cc51b374", "redis://:" "abcdefgh"),
    ("4db6b610d828", "postgresql://lumen:" "fixturesecret99"),
    ("69e45220f762", "redis://:" "anotherfixture77"),
]


def main():
    ok = True
    for sha12, whole in NAMED:
        body = whole.split(":", 2)[-1]
        got = hashlib.sha256(body.encode()).hexdigest()[:12]
        print(sha12, "named" if got == sha12 else "MISMATCH")
        ok &= got == sha12
    with open(__file__) as fh:
        source = fh.read()
    fired = masking.mask_text(source)[1]
    clean = not fired
    print("self-mask", "clean" if clean else "FIRES %s" % fired)
    return 0 if ok and clean else 1


if __name__ == "__main__":
    sys.exit(main())
