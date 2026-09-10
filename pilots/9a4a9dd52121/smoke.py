import sys
sys.path.insert(0, "/home/lumen/foreman/prompt-forge")
from run_pilot import call, DRAFT_MODEL, TARGET_MODEL

for m in (DRAFT_MODEL, TARGET_MODEL):
    text, lat = call(m, "In one sentence, what is the capital of France?", 60)
    print(m, round(lat, 2), repr(text)[:200])
