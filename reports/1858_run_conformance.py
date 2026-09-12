import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/lumen/research/harnessrouter-ref/src/protocol/conformance")
from uhp_conformance import cli

token_path = Path(os.environ.get("DESPAG_API_TOKEN_FILE", "~/.secrets/despag-api.token")).expanduser()
os.environ["UHP_API_KEY"] = token_path.read_text().strip()

sys.argv = [
    "uhp-conformance",
    "--base-url", "http://127.0.0.1:8791",
    "--class", "core",
    "--only", "D-01,D-02,D-03,D-04,D-05,H-01,H-02,H-03,H-04,T-01,T-02,T-03,T-04,T-05,T-06,T-07,T-08,T-09,T-10,S-08",
    "--harness-id", "litellm-mechanical",
    "--plain",
    "--json", "/home/lumen/worktrees/wq1858-a1/reports/1858_verify_conformance.json",
]
sys.exit(cli.main())
