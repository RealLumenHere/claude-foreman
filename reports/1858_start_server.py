import os
import sys
os.environ["DESPAG_PORT"] = "8791"
os.chdir("/home/lumen/worktrees/wq1858-a1")
sys.path.insert(0, "/home/lumen/worktrees/wq1858-a1")
import uvicorn
uvicorn.run("despag.app:app", host="127.0.0.1", port=8791, log_level="info")
