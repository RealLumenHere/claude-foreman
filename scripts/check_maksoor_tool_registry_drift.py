"""wq#4012: TOOL_REGISTRY.md's Maksoor tool table is hand-maintained prose, so it already
drifted from lumen_state once (missing the 2026-10-13 Phase 1-3 install:
`maksoor.re_toolset`). Run this after any future maksoor.re_toolset update — it fails
if a lumen_state-reported tool name isn't mentioned in the Maksoor section, instead of
letting that drift sit unnoticed again."""
import re
import subprocess
import sys

LUMEN_STATE = "/home/lumen/bin/lumen_state.py"
REGISTRY_PATH = "TOOL_REGISTRY.md"
SECTION_HEADER = "## Maksoor RE workstation"


def lumen_state_fact(subject, predicate):
    out = subprocess.run(
        [LUMEN_STATE, "current", subject, predicate],
        capture_output=True, text=True, check=True,
    ).stdout
    match = re.search(r"=\s*(.*?)\s{2,}\(since ", out, re.S)
    return (match.group(1) if match else out).strip()


def phase13_tool_names(fact_text):
    install_clause = fact_text.split(". DEFERRED:")[0]
    install_clause = re.sub(r"^RE Phase 1-3 installed [\d-]+:\s*", "", install_clause)
    names = []
    for chunk in re.split(r",\s*", install_clause):
        for piece in chunk.split("+"):
            name_match = re.match(r"\s*([A-Za-z]+)", piece)
            if name_match:
                names.append(name_match.group(1))
    return names


def maksoor_section(registry_text):
    start = registry_text.index(SECTION_HEADER)
    rest = registry_text[start + len(SECTION_HEADER):]
    end_match = re.search(r"\n## ", rest)
    end = start + len(SECTION_HEADER) + (end_match.start() if end_match else len(rest))
    return registry_text[start:end]


def main():
    fact_text = lumen_state_fact("maksoor", "re_toolset")
    names = phase13_tool_names(fact_text)
    registry_text = open(REGISTRY_PATH).read()
    section = maksoor_section(registry_text).lower()
    missing = sorted({n for n in names if n.lower() not in section})
    if missing:
        print(f"DRIFT: {SECTION_HEADER} is missing tools lumen_state's maksoor.re_toolset "
              f"says are installed: {', '.join(missing)}")
        return 1
    print(f"OK: all {len(set(names))} distinct maksoor.re_toolset Phase 1-3 tool names are "
          f"present in the {SECTION_HEADER} section")
    return 0


if __name__ == "__main__":
    sys.exit(main())
