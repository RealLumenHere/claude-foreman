"""wq#4413: three straight judgment rounds on the NanoBot dissection each fixed the
previous round's transcribed number and introduced a new one (wq#4401 E1/E5, wq#4404's
fabrication call, wq#4406's "39 of 46"). The numbers are all derivable from one
rev-pinned file, so derive them: this parses HKUDS/nanobot's providers/registry.py with
`ast`, counts the PROVIDERS catalog tuple, and fails if the figures asserted in
research/WQ4351_NANOBOT_DISSECTION.md or reports/wq4406_judgment_wq4404_nanobot_citations.md
no longer match. Run it before changing any count in those docs.

  python3 -I scripts/wq4413_count_nanobot_providerspecs.py            # fetch pinned rev
  python3 -I scripts/wq4413_count_nanobot_providerspecs.py --registry /tmp/registry.py

Exit 0 = counts derived and every doc claim matches; exit 1 = drift (or a bad fetch)."""
import argparse
import ast
import collections
import hashlib
import pathlib
import re
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

REV = "909b277e"
RAW_URL = (
    "https://raw.githubusercontent.com/HKUDS/nanobot/"
    f"{REV}/nanobot/providers/registry.py"
)
# sha256 of that URL's body, captured 2026-10-19. A rev-pinned raw path is immutable,
# so a mismatch means the fetch was tampered with or redirected, not that upstream moved.
REGISTRY_SHA256 = "ad0f38fbad5c89b1fa46a5fb6c84e2b9538584b7db82345372e90b28c222de43"

CATALOG_NAME = "PROVIDERS"
SPEC_CALL = "ProviderSpec"
# the docs spell small counts out, so claim-matching needs the word as well as the digit
NUMBER_WORDS = {7: "seven", 8: "eight"}


def fetch_registry():
    body = subprocess.run(
        ["curl", "-sS", "--fail", RAW_URL], capture_output=True, check=True
    ).stdout
    digest = hashlib.sha256(body).hexdigest()
    if digest != REGISTRY_SHA256:
        sys.exit(
            f"FAIL: {RAW_URL}\n  expected sha256 {REGISTRY_SHA256}\n  got      {digest}"
        )
    return body.decode()


def catalog_specs(tree):
    """The ProviderSpec(...) calls inside the `PROVIDERS` tuple literal, in order."""
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        if not any(isinstance(t, ast.Name) and t.id == CATALOG_NAME for t in targets):
            continue
        if not isinstance(node.value, ast.Tuple):
            sys.exit(f"FAIL: {CATALOG_NAME} is not a tuple literal at line {node.lineno}")
        return node, [
            el
            for el in node.value.elts
            if isinstance(el, ast.Call)
            and isinstance(el.func, ast.Name)
            and el.func.id == SPEC_CALL
        ]
    sys.exit(f"FAIL: no {CATALOG_NAME} assignment found")


def keyword_str(call, name):
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return None


def spec_calls_file_wide(tree):
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == SPEC_CALL
    ]


def documented_backends(source):
    """The adapter names enumerated in the comment above ProviderSpec's `backend` field.
    It is annotated plain `str`, so the comment — not the type — is what documents the
    seven adapters; return the names and the line range they span."""
    lines = source.splitlines()
    for index, line in enumerate(lines):
        if not line.strip().startswith("backend:"):
            continue
        first = index
        while first > 0 and lines[first - 1].strip().startswith("#"):
            first -= 1
        # span only the comment lines that actually carry a quoted adapter name — the
        # lead-in line ("# which provider implementation to use") is not the enumeration
        quoted = {
            lineno: re.findall(r'"([a-z0-9_]+)"', lines[lineno])
            for lineno in range(first, index)
        }
        carrying = sorted(lineno for lineno, names in quoted.items() if names)
        if carrying:
            names = [name for lineno in carrying for name in quoted[lineno]]
            return names, (carrying[0] + 1, carrying[-1] + 1)
    return [], None


def derive(source):
    tree = ast.parse(source)
    catalog_node, specs = catalog_specs(tree)
    backends = collections.Counter(keyword_str(s, "backend") for s in specs)
    enum, enum_span = documented_backends(source)
    all_specs = spec_calls_file_wide(tree)
    outside = [
        s
        for s in all_specs
        if not catalog_node.lineno <= s.lineno <= catalog_node.end_lineno
    ]
    return {
        "catalog_span": (catalog_node.lineno, catalog_node.end_lineno),
        "catalog_entries": len(specs),
        "backends": backends,
        "enum": enum,
        "enum_span": enum_span,
        "spec_calls_file_wide": len(all_specs),
        "outside_catalog": [(s.lineno, s.end_lineno) for s in outside],
    }


def doc_claims(facts):
    """(path, exact substring that must appear) — built from the derived numbers, so an
    off-by-one in either doc makes the substring absent and this script exit 1."""
    catalog = facts["catalog_entries"]
    compat = facts["backends"]["openai_compat"]
    adapters = NUMBER_WORDS.get(len(facts["enum"]), str(len(facts["enum"])))
    enum_lines = "registry.py:{}-{}".format(*facts["enum_span"])
    research = "research/WQ4351_NANOBOT_DISSECTION.md"
    judgment = "reports/wq4406_judgment_wq4404_nanobot_citations.md"
    return [
        (research, f"**{catalog}** declarative `ProviderSpec` entries"),
        (research, f"{compat} of the {catalog} specs ride `openai_compat`"),
        # the E5 errata row words the same ratio differently; wq#4413's own grep for
        # "39 of the 46" missed it, so pin this phrasing explicitly too
        (research, f"one of {compat} such specs out of {catalog}"),
        (research, f"**{adapters}** native backend adapters"),
        (research, f"`nanobot/providers/{enum_lines}`"),
        (judgment, f"as do {compat} of the {catalog}"),
        (judgment, f"{adapters} values (`{enum_lines}`"),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry", help=f"local copy of registry.py @ {REV} (default: fetch pinned raw URL)"
    )
    args = parser.parse_args()

    if args.registry:
        with open(args.registry, encoding="utf-8") as handle:
            source = handle.read()
    else:
        source = fetch_registry()

    facts = derive(source)
    span = facts["catalog_span"]
    print(f"registry.py @ {REV}")
    print(f"  {CATALOG_NAME} tuple        : lines {span[0]}-{span[1]}")
    print(f"  catalog {SPEC_CALL} entries : {facts['catalog_entries']}")
    print(f"  {SPEC_CALL} calls file-wide : {facts['spec_calls_file_wide']}"
          f" ({len(facts['outside_catalog'])} outside the catalog: "
          f"{', '.join(f'{a}-{b}' for a, b in facts['outside_catalog']) or 'none'})")
    enum_span = facts["enum_span"]
    print(f"  backends documented at registry.py:{enum_span[0]}-{enum_span[1]}: "
          f"{len(facts['enum'])} -> {', '.join(facts['enum'])}")
    for backend, count in facts["backends"].most_common():
        print(f"    {backend:<14}: {count}")

    failures = []
    for path, needle in doc_claims(facts):
        try:
            text = (REPO_ROOT / path).read_text(encoding="utf-8")
        except OSError as exc:
            failures.append(f"{path}: {exc}")
            continue
        if needle not in text:
            failures.append(f'{path}: missing derived claim "{needle}"')

    if failures:
        print("\nDOC DRIFT:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print(f"\nOK: {len(doc_claims(facts))} doc claims match the derived counts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
