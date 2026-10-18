"""Write PROVENANCE.json for an install.sh run (wq#4206).
argv: DEST REPO PIN PLAYWRIGHT CHROME_PATH CHROME_VERSION PIP_FREEZE"""
import datetime
import hashlib
import json
import sys
from pathlib import Path

dest, repo, pin, playwright, chrome, chrome_version, freeze = sys.argv[1:8]
dest = Path(dest)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


record = {
    "ticket": "wq#4206",
    "installed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "upstream": {"repo": repo, "commit": pin, "license": "MIT",
                 "requirements_sha256": sha256(dest / "repo" / "requirements.txt")},
    "browser": {"source": f"playwright=={playwright} install chromium",
                "path": chrome, "version": chrome_version, "sha256": sha256(chrome)},
    "launcher_sha256": {name: sha256(dest / name)
                        for name in ("lumen_stealth_mcp.py", "lumen_stealth_guard.py")},
    "venv_freeze": freeze.splitlines(),
}
(dest / "PROVENANCE.json").write_text(json.dumps(record, indent=2) + "\n")
print(f"wrote {dest / 'PROVENANCE.json'}")
