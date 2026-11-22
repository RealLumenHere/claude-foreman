"""wq#5221 probe: every in-file #anchor link in a README resolves to a heading
(GitHub slug rules), and the wq#5220 paragraph sits inside '## Running a ticket'.
Usage: python3 anchor_probe.py <git-rev> [extra-anchor-that-must-fail]"""
import re, subprocess, sys

rev = sys.argv[1]
text = subprocess.run(["git", "-C", "/home/lumen/foreman", "show", f"{rev}:harness/README.md"],
                      capture_output=True, text=True, check=True).stdout
lines = text.splitlines()

def slug(h):
    s = h.strip().lower()
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")

in_code = False
slugs = {}
heads = []
for i, l in enumerate(lines, 1):
    if l.startswith("```"):
        in_code = not in_code
        continue
    m = None if in_code else re.match(r"^(#{1,6})\s+(.*)$", l)
    if m:
        s = slug(m.group(2))
        n = slugs.get(s, 0)
        slugs[s] = n + 1
        heads.append((i, len(m.group(1)), m.group(2), s if n == 0 else f"{s}-{n}"))
valid = {h[3] for h in heads}
links = [(i, a) for i, l in enumerate(lines, 1) for a in re.findall(r"\]\(#([^)]+)\)", l)]
extra = sys.argv[2:]
bad = [(i, a) for i, a in links if a not in valid] + [("extra", a) for a in extra if a not in valid]
print("links:", links)
print("unresolved:", bad)

# placement: paragraph mentioning fworker_sdk.py link must be inside 'Running a ticket'
idx = next(i for i, l in enumerate(lines, 1) if "](#sdk-runtime-" in l)
owner = [h for h in heads if h[0] < idx and h[1] <= 2][-1]
print("link line", idx, "owned by section:", owner[2])
ok = not bad and owner[2] == "Running a ticket"
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
