"""wq#4048 -- mechanical enforcement of the catalog's license-risk policy.

tool-catalog.json's ``provenance.license_risk_policy`` (wq#3959) says every tool
entry going forward MUST carry ``license_risk`` and ``redistribution_posture``,
because the free-text ``license`` string alone does not say whether a tool may be
bundled into the commercially-sold Limpet ISO. That policy was prose only: wq#3972
added tshark (GPL-2.0) and afl++ (Apache-2.0) at trust=core -- i.e. into the base
image -- with neither field, and nothing failed. The gap surfaced only in the
wq#4047 verify pass.

This module turns the prose into a check. It is a *ratchet*, not a full audit: the
41 entries that predate the policy are frozen in :data:`LEGACY_UNAUDITED` and
exempted, so the backlog can be audited on its own ticket while any entry added or
renamed from here on must carry both fields. Shrinking that set is the only
sanctioned edit to it; adding to it re-opens the hole wq#4048 closed.
"""

from __future__ import annotations

import json
import pathlib

DEFAULT_CATALOG = pathlib.Path(__file__).parent / "tool-catalog.json"

LICENSE_RISKS = frozenset(
    {"none", "copyleft-source-offer", "non-osi-commercial-restricted", "unverified"}
)
REDISTRIBUTION_POSTURES = frozenset(
    {"bundle", "external-invoke", "blocked-pending-permission"}
)

# Postures that may reach the shipped ISO. Anything else is catalogued for
# external invocation or is blocked pending a written commercial license.
BUNDLEABLE_POSTURES = frozenset({"bundle"})

# Entries that predate wq#3959's policy and have not been license-audited yet.
# Frozen by wq#4048 as the ratchet baseline -- remove names as they are audited,
# never add new ones.
LEGACY_UNAUDITED = frozenset(
    {
        "unblob", "binwalk", "fwanalyzer", "fwtriage", "radare2", "llm4decompile",
        "ifda", "cutter", "frida", "qemu-user", "qemu-system", "gdb-multiarch",
        "qiling", "halucinator", "firmwire", "fuzzware", "pretender", "openocd",
        "flashrom", "rkdeveloptool", "mtkclient", "edl", "heimdall",
        "spreadtrum_flash", "hackrf", "sigrok-cli", "sootty", "netzob", "jadx",
        "apktool", "scrcpy", "rea", "ghidra-mcp", "searchsploit", "component-id",
        "fw-diff", "gepetto", "pyamlboot", "sunxi-fel", "fiedka", "glitchkit",
    }
)


def check_entry(entry: dict) -> list[str]:
    """Return policy violations for one catalog entry (empty list = compliant)."""
    name = entry.get("name") or "<unnamed>"
    problems = []
    risk = entry.get("license_risk")
    posture = entry.get("redistribution_posture")

    if risk is None or posture is None:
        if name not in LEGACY_UNAUDITED:
            missing = [
                field
                for field, value in (
                    ("license_risk", risk),
                    ("redistribution_posture", posture),
                )
                if value is None
            ]
            problems.append(
                f"{name}: missing {' and '.join(missing)} "
                "(tool-catalog.json provenance.license_risk_policy, wq#3959)"
            )
    if risk is not None and risk not in LICENSE_RISKS:
        problems.append(
            f"{name}: license_risk={risk!r} not one of {sorted(LICENSE_RISKS)}"
        )
    if posture is not None and posture not in REDISTRIBUTION_POSTURES:
        problems.append(
            f"{name}: redistribution_posture={posture!r} not one of "
            f"{sorted(REDISTRIBUTION_POSTURES)}"
        )
    return problems


def check_catalog(tools: list[dict]) -> list[str]:
    """Return every policy violation across a catalog's tool list."""
    return [problem for entry in tools for problem in check_entry(entry)]


def load_tools(catalog_path: pathlib.Path = DEFAULT_CATALOG) -> list[dict]:
    with open(catalog_path, encoding="utf-8") as fh:
        return json.load(fh)["tools"]


def is_bundleable(entry: dict) -> bool:
    """True if this entry may be installed into the ISO.

    An entry with no posture at all is a legacy row the ISO builder already
    shipped before wq#3959 existed; withholding those would silently shrink the
    image, so only an explicit non-bundle posture excludes a tool.
    """
    posture = entry.get("redistribution_posture")
    return posture is None or posture in BUNDLEABLE_POSTURES


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", type=pathlib.Path, default=DEFAULT_CATALOG)
    args = parser.parse_args(argv)

    problems = check_catalog(load_tools(args.catalog))
    for problem in problems:
        print(problem)
    print(f"{len(problems)} license-policy violation(s) in {args.catalog}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
