import json
import re
import subprocess
import sys
import time

BASE = "100.100.129.24:19999"
after, before, points, pat = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]


def get(url):
    return json.loads(subprocess.run(["curl", "-s", "-m", "20", url], capture_output=True, text=True).stdout)


charts = sorted(c for c in get(f"http://{BASE}/api/v1/charts")["charts"] if re.search(pat, c))
print("charts:", len(charts), [c.split("_")[-2] if c.endswith("_input") else c for c in charts][:12])
series = {}
for c in charts[:8]:
    d = get(f"http://{BASE}/api/v1/data?chart={c}&after={after}&before={before}&points={points}&group=average&format=json&options=seconds")
    label = c.split("_")[-2] if c.endswith("_input") else c[-14:]
    series[label] = {int(r[0]): round(sum(v for v in r[1:] if v is not None), 1) for r in d.get("data", []) if any(v is not None for v in r[1:])}
stamps = sorted(set().union(*[set(s) for s in series.values()])) if series else []
print("timeZ", *series)
for ts in stamps:
    print(time.strftime("%H:%M", time.gmtime(ts)), *[series[l].get(ts, "-") for l in series])
