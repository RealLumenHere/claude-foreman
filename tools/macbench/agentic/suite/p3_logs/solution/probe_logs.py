import collections
import sys

counts = collections.Counter()
for line in open(sys.argv[1]):
    f = line.split(None, 2)
    if len(f) >= 2 and f[1] == "ERROR":
        counts[f[0][:13]] += 1
for hour in sorted(counts):
    print(hour, counts[hour])
