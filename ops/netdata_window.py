#!/usr/bin/env python3
"""Merge several netdata charts over one time window into a single table.

wq#2140: netdata on isis retains applesmc temps + fan rpm across the reboot, so
the thermal trace of a boot that ended in a hard power cut can be read back
without reproducing the crash.

  netdata_window.py <host:port> <after-epoch> <before-epoch> <points> <chart>[=label] ...
"""
import json
import subprocess
import sys
import time


def fetch(base, chart, after, before, points):
    url = (
        f"http://{base}/api/v1/data?chart={chart}&after={after}&before={before}"
        f"&points={points}&group=average&format=json&options=seconds"
    )
    raw = subprocess.run(["curl", "-s", "-m", "30", url], capture_output=True, text=True, timeout=60).stdout
    doc = json.loads(raw)
    # dimension values are summed: these charts carry a single dimension each.
    out = {}
    for row in doc.get("data", []):
        vals = [v for v in row[1:] if v is not None]
        out[int(row[0])] = round(sum(vals), 1) if vals else None
    return out


def main():
    base, after, before, points = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    series, labels = {}, []
    for spec in sys.argv[5:]:
        chart, _, label = spec.partition("=")
        label = label or chart.split("_")[-2]
        labels.append(label)
        series[label] = fetch(base, chart, after, before, points)
    stamps = sorted(set().union(*[set(s) for s in series.values()]))
    print("timeZ," + ",".join(labels))
    for ts in stamps:
        row = [series[l].get(ts) for l in labels]
        if all(v is None for v in row):
            continue
        cells = ",".join("" if v is None else str(v) for v in row)
        print(f"{time.strftime('%m-%dT%H:%M:%S', time.gmtime(ts))},{cells}")


if __name__ == "__main__":
    main()
