import json
import sys

binds = {}
for line in open(sys.argv[1]).read().splitlines()[1:]:
    f = line.split()
    if len(f) >= 4 and f[0] == "LISTEN":
        addr, _, port = f[3].rpartition(":")
        binds.setdefault(int(port), set()).add(addr)
loop = {"127.0.0.1", "[::1]"}
print(json.dumps({"ports": sorted(binds),
                  "loopback_only": sorted(p for p, a in binds.items() if a <= loop)}))
