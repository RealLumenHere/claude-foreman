"""wq#2698: name every value wq#2697's run was flagged for, by hash, from committed text.

wq#2697 wrote seam tests for bin/mask-secrets and ran them red before its fix. Its draft
parametrize lists had no `ids=`, so pytest printed each fixture as the test id
(`[<live>-<masked>-<residue>]`), and a draft residue id put the words `after-<rule>`
behind the `sk-key` id. It also printed the pre-fix tool's output, `cfut_` glued to two
mask literals. Every value mask-hits-1.json and `mask_replay.py 2697` flagged is one of
those strings. This script rebuilds each one from the fixture constants committed in
cf4ebed (rev-pinned, so it re-runs identically) and matches it by sha256, printing only
hashes and recipes, never a value.

Exit 0 when every target is named, 1 otherwise.
"""
import ast
import os
import subprocess
import sys

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "harness")
sys.path.insert(0, HARNESS)
import masking  # noqa: E402
import mask_replay  # noqa: E402

PIN = "cf4ebed"
TEST = "ops/wq2316-mask-secrets-boundary/test_mask_secrets_boundary.py"

# mask-hits-1.json (the ticket's 4 values + the github one) and the three
# `mask_replay.py 2697` reported BURNED? before this ticket's fix.
TARGETS = {
    "76b4100ba253": "hits-file github",
    "d500baeba1c4": "hits-file sk-key",
    "f39dac6cbaba": "hits-file sk-key",
    "d336dad0c489": "hits-file slack",
    "e69bbe113521": "hits-file aws",
    "6c7b9a1502d8": "replay cloudflare",
    "dc391d0afd87": "replay cloudflare",
    "d7ca301630a0": "replay sk-key",
}


def pinned_constants():
    """{name: str} for the module-level plain-literal constants of the test at PIN."""
    src = subprocess.run(["git", "-C", os.path.join(HARNESS, ".."), "show",
                          f"{PIN}:{TEST}"], capture_output=True, text=True,
                         check=True).stdout
    consts = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            try:
                consts[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                continue
    return consts


def candidates(c):
    """(recipe, text) for every string wq#2697's red runs could have printed."""
    lives = [("ghp_" + c["_GH_BODY"], "ghp_MASKED"), ("AKIA" + "Q" * 16, "AKIAMASKED"),
             ("xoxb-" + "12345678901", "xoxb-MASKED"),
             ("sk-ant-" + c["_SK_BODY"], "sk-ant-MASKED")]
    for live, masked in lives:
        for residue in ("ghp_MASKED", "MASKED", "cfut_MASKED"):
            yield f"default id live-{masked}-{residue}", f"[{live}-{masked}-{residue}]"
    # a 2 KB persisted-output preview cut the sk-ant default id mid-body
    yield "default id, preview-truncated", "[sk-ant-" + c["_SK_BODY"][:16] + "\n"
    # A variable, not a literal: mask_replay seam-joins `"a" + "b"`, and the joined id
    # in this file would then account the value as committed text instead of the
    # word_run screen that actually explains it.
    rule = "cloudflare"
    for name in ("github", "aws", "slack", "sk-key"):
        yield f"id {name}-after-<rule>", f"[{name}-after-{rule}]"
    for glue in ("xoxb-MASKED", "sk-ant-MASKED"):
        yield f"pre-fix output cfut_MASKED{glue}", "- cfut_MASKED" + glue


def main():
    named = {}
    for recipe, text in candidates(pinned_constants()):
        values = [v for _, _, v in mask_replay._hits(mask_replay.LEGACY_PATTERNS, text,
                                                     skip_masked_output=False)]
        values += [v for _, _, v in mask_replay._hits(mask_replay.CURRENT_PATTERNS, text,
                                                      skip_masked_output=True)]
        shas = [masking.value_sha12(v) for v in values]
        # the tripwire's own cut (masking.scan), which wrote mask-hits-1.json
        shas += [r["value_sha256_12"] for r in masking.scan(text)[1]]
        for sha in shas:
            named.setdefault(sha, recipe)
    missing = 0
    for sha, where in TARGETS.items():
        print(f"{sha} ({where}): {named.get(sha, 'UNNAMED')}")
        missing += sha not in named
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
