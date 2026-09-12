"""wq#1805: `python3 -m despag` runs the discovery/harness/model surface on
127.0.0.1:$DESPAG_PORT (default 8790). Loopback only — TLS/exposure is a
deploy-time concern, not this process's."""
import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("DESPAG_PORT", "8790"))
    uvicorn.run("despag.app:app", host="127.0.0.1", port=port, log_level="info")
