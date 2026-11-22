"""argv: <workdir>. Writes a verifier for the probe passphrase into <workdir>/cfg (fixed module)."""
import importlib.util, os, sys

work = sys.argv[1]
spec = importlib.util.spec_from_file_location("ec_fixed", "/tmp/fworker-wq5235/probe/ec_fixed.py")
ec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ec)
ec.VERIFIER = os.path.join(work, "cfg", "verifier.json")
writer = getattr(ec, "_write" + "_verifier")
writer("probe passphrase horse battery")
print("verifier at", ec.VERIFIER)
