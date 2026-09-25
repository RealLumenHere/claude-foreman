"""wq#2638 second pass: masked residue glued in FRONT of a live value.

Re-runnable evidence for the claim the fix rests on, in two parts.

CLASSIFY — is the silent miss a regression? Runs one shape, `<prefix>MASKED<prefix><live
body>`, against three revisions of harness/masking.py extracted by `git show`: pre-wq#2298
(99ae7fb^), pre-wq#2638 (e681514^) and the working tree. cloudflare, slack and aws mask
and alarm at both older revisions and went silent at e681514 — the bounded body stopped
the leading match at the literal, and `_B` then refused the live half because the
character in front of it is the `D` of our own output. github is a pre-existing miss at
all three, since its body class cannot cross the `_` of its own prefix either way.

CORPUS — what does the fix cost on text nobody wrote for it (wq#2630)? The input is a
rev-pinned read of kb/ISSUE-LOG.md, the doctrine every ticket reads, which is ~900 lines
of adversarial prose ABOUT credential shapes and quotes this masker's own output in its
rotation rows. Two directions, and both can fail: the unseamed hit set must be identical
to the pre-fix table's (a new hit is a false positive the boundary exception bought), and
with our own output plus a live value seamed onto each line the tree must be blind on
none — while the pre-fix table, scored on the same lines, is blind on most of them. A
probe that could only confirm would report the second number as zero.

Bodies here are synthetic and built by concatenation, so no credential shape is written
into a tracked file (test_token_injection.py's tripwire, wq#2299).

    python3 ops/wq2638-adjacency/probe_adjacency.py
"""
import importlib.util
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "harness"))      # live_code, imported by masking

# kb is a separate repo, pinned so a later edit to the doctrine cannot move the result.
KB = "/home/lumen/kb"
KB_REV = "de4e158158aef5b94e13c09e96205cf87d0dea31"

# (label, kept prefix, live body) — each body clears its rule's floor.
RULES = [
    ("github", "ghp_", "A" + "b3Cd" * 9),
    ("cloudflare", "cfut_", "A" + "b3Cd" * 5),
    ("sk-key", "sk-ant-", "A" + "b3Cd" * 5),
    ("slack", "xoxb-", "A" + "b3Cd" * 5),
    ("aws", "AKIA", "Q" * 16),
]


def show(repo, rev, path):
    return subprocess.run(["git", "-C", repo, "show", f"{rev}:{path}"],
                          capture_output=True, text=True, check=True).stdout


def load(name, source, workdir):
    """Import `source` under `name`, from a file so masking's own fingerprint works."""
    path = os.path.join(workdir, name + ".py")
    with open(path, "w") as fh:
        fh.write(source)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def seam(mod, prefix, body):
    """Our own output for a rule, then a live value of it, glued with no separator."""
    return prefix + getattr(mod, "MASK", "MASKED") + prefix + body


def classify(revs):
    print("CLASSIFY — `<prefix>MASKED<prefix><live>`, per revision")
    for name, mod in revs:
        verdicts = []
        for label, prefix, body in RULES:
            masked, hits = mod.mask_text(seam(mod, prefix, body))
            verdicts.append(f"{label}={'LEAK' if body in masked else 'masked'}")
            del hits
        print(f"  {name:22s} " + "  ".join(verdicts))


def corpus(before, after):
    lines = show(KB, KB_REV, "ISSUE-LOG.md").splitlines()
    print(f"\nCORPUS — kb/ISSUE-LOG.md @ {KB_REV[:7]}, {len(lines)} lines")

    def hits(mod):
        return {(n, label) for n, line in enumerate(lines, 1)
                for label in mod.mask_text(line)[1]}

    old, new = hits(before), hits(after)
    print(f"  unseamed: pre-fix fires on {len(old)}, tree on {len(new)}; "
          f"new {len(new - old)}, lost {len(old - new)} "
          f"-> {'unchanged' if old == new else 'CHANGED'}")

    blind_new = blind_old = 0
    for line in lines:
        for label, prefix, body in RULES:
            text = line + seam(after, prefix, body)
            masked, got = after.mask_text(text)
            blind_new += body in masked or label not in got
            blind_old += body in before.mask_text(text)[0]
    total = len(lines) * len(RULES)
    print(f"  seamed:   {total} lines; tree blind on {blind_new}, "
          f"pre-fix blind on {blind_old}")
    return old == new and blind_new == 0 and blind_old > 0


def main():
    with tempfile.TemporaryDirectory() as workdir:
        revs = [
            ("pre-wq2298 (99ae7fb^)",
             load("m_pre2298", show(REPO, "99ae7fb^", "harness/masking.py"), workdir)),
            ("pre-wq2638 (e681514^)",
             load("m_pre2638", show(REPO, "e681514^", "harness/masking.py"), workdir)),
            ("pre-this-pass (HEAD)",
             load("m_head", show(REPO, "HEAD", "harness/masking.py"), workdir)),
            ("tree",
             load("m_tree", open(os.path.join(REPO, "harness/masking.py")).read(),
                  workdir)),
        ]
        classify(revs)
        ok = corpus(dict(revs)["pre-this-pass (HEAD)"], dict(revs)["tree"])
    print("\nverdict:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
