import json
import sys

rows = []
for line in open(sys.argv[1]).read().splitlines()[1:]:
    f = line.split()
    if len(f) >= 6 and f[4].endswith("%") and int(f[4][:-1]) >= 80:
        rows.append({"mount": f[5], "use_pct": int(f[4][:-1])})
rows.sort(key=lambda r: (-r["use_pct"], r["mount"]))
print(json.dumps(rows))
