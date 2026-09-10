import sys
sys.path.insert(0, "/home/lumen/foreman/prompt-forge")
import llm
import urllib.request
import json

key = llm._api_key()
print("key_available:", key is not None)
req = urllib.request.Request(
    "http://127.0.0.1:4000/v1/models",
    headers={"Authorization": "Bearer " + key},
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    ids = sorted(m["id"] for m in data.get("data", []))
    print(len(ids), "models")
    for i in ids:
        print(i)
except Exception as e:
    print("ERR", type(e).__name__, str(e)[:300])
