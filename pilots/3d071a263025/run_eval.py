import importlib.util
import json
import sys

REPO = "/home/lumen/foreman/pilots/3d071a263025/work/OmegaUse-OfficeVal/verifiers"


def load_verifier(task_id):
    mod_name = task_id + "_verifier"
    spec = importlib.util.spec_from_file_location(
        mod_name, REPO + "/" + mod_name + ".py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    task_id = sys.argv[1]
    dir_path = sys.argv[2]
    mod = load_verifier(task_id)
    result = mod.evaluate(dir_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
