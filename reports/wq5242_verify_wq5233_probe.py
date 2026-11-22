"""wq#5242: live probe of the serial-maksoor bridge's INSTALLED energize_confirm.py copy.

An unsigned, pre-wq5216-style token file (the record format the old ef722eba copy accepted) and a
v-tagged record with a junk signature must both be refused. Exit 0 = both refused.

    python3 reports/wq5242_verify_wq5233_probe.py                       # installed copy (expect exit 0)
    python3 reports/wq5242_verify_wq5233_probe.py <old energize_confirm.py>  # pre-wq5216 copy: legacy accepted

Token files go to a private tempdir (TOKEN_DIR is monkeypatched), so the live token dir is untouched.
"""
import importlib.util
import json
import sys
import tempfile
import time

DEFAULT = "/home/lumen/.local/share/mcp/serial-maksoor/energize_confirm.py"
path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
spec = importlib.util.spec_from_file_location("ec_probe", path)
ec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ec)
ec.TOKEN_DIR = tempfile.mkdtemp(prefix="wq5242-")
tok = "a" * 64
cap = "serial.write"


def try_record(rec: dict) -> bool:
    with open(ec._token_path(tok), "w") as f:
        json.dump(rec, f)
    return ec.verify_token(tok, cap)


legacy = try_record({"capability": cap, "expires_at": int(time.time()) + 300})
junk = False
if hasattr(ec, "RECORD_VERSION"):
    junk = try_record({"v": ec.RECORD_VERSION, "capability": cap,
                       "expires_at": int(time.time()) + 300, "sig": "00" * 64})
print("path", path)
print("unsigned_legacy_accepted", legacy)
print("junk_sig_accepted", junk)
sys.exit(0 if (legacy is False and junk is False) else 1)
