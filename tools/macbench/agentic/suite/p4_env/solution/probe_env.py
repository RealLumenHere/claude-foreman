import json
import sys

names = [ln.strip() for ln in open(sys.argv[1]) if ln.strip() and not ln.startswith("#")]
env = json.load(open(sys.argv[2]))
missing = sorted(n for n in names if not env.get(n))
if missing:
    print("\n".join(missing))
    sys.exit(1)
print("ok")
