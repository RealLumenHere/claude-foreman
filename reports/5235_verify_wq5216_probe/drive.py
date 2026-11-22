"""wq#5235 verify probe driver. argv: <module.py> <workdir> <cli args...>
Loads a rev-pinned energize_confirm copy, points its dirs at <workdir>, runs its CLI.
Prints MINTED <n> = number of token records in the token dir afterwards."""
import importlib.util, os, sys

mod_path, work = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("ec_under_test", mod_path)
ec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ec)
ec.TOKEN_DIR = os.path.join(work, "tokens")
if hasattr(ec, "VERIFIER"):
    ec.VERIFIER = os.path.join(work, "cfg", "verifier.json")
    ec.FAIL_DELAY = 0
rc = ec._main(sys.argv[3:])
n = len(os.listdir(ec.TOKEN_DIR)) if os.path.isdir(ec.TOKEN_DIR) else 0
print(f"RC {rc} MINTED {n}", flush=True)
