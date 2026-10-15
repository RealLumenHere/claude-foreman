"""Acceptance probe for design/limpet/plugin-manifest.schema.json (wq#3806).

Positive cases are the two example manifests from projects/limpet/VISION.md
(ghidra tool plugin, magewell hardware plugin) translated TOML->JSON.
Negative cases must be rejected: an invalid trust tier, a hardware-category
plugin missing its [hardware] block, and mcp.enabled=true without a server.
Run: python3 -I design/limpet/test_plugin_manifest_schema.py
Exits non-zero on any failure, so it can fail.
"""
import json
import os
import sys

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "plugin-manifest.schema.json")

GHIDRA = {
    "plugin": {"name": "ghidra", "version": "12.1.3", "category": "disassembler",
               "description": "NSA SRE framework"},
    "trust": "core",
    "capabilities": {"disassemble": True, "decompile": True,
                     "architectures": ["x86", "ARM", "MIPS"],
                     "input_formats": ["ELF", "PE"],
                     "output_formats": ["C", "JSON"]},
    "mcp": {"enabled": True, "server": "ghidra-mcp", "transport": "stdio"},
    "paths": {"binary": "/opt/ghidra/ghidraRun",
              "headless": "/opt/ghidra/support/analyzeHeadless"},
    "health": {"check": "/opt/ghidra/support/analyzeHeadless --version",
               "interval": "1h"},
    "dependencies": {"requires": ["java-21"]},
}

MAGEWELL = {
    "plugin": {"name": "magewell-hdmi", "version": "2", "category": "hardware",
               "description": "Magewell USB Capture HDMI Gen 2"},
    "trust": "core",
    "hardware": {"type": "video-capture",
                 "detection": {"usb_vendor": "2935", "usb_product": "0001",
                               "device_path": "/dev/video*",
                               "driver": "uvcvideo"},
                 "agent_use": {"visual_input": True, "screen_reading": True}},
    "capabilities": {"capture_video": True, "capture_audio": True},
}

# wq#4056: a fully trust-gated manifest — pinned hash + provenance + signature.
SIGNED = {
    "plugin": {"name": "ghidra-mcp", "version": "0.3.1", "category": "decompiler"},
    "trust": "vetted",
    "integrity": {"algo": "sha256", "sha256": "9f" * 32},
    "provenance": {"source": "https://github.com/example/limpet-ghidra-mcp",
                   "commit": "4a" * 20, "build": "git-clone",
                   "builder": "limpet-ci", "fetched_at": "2026-10-15"},
    "signature": {"scheme": "minisign", "key_id": "RWQf6LRCGA9i5",
                  "signer": "maintainer", "file": "limpet-plugin.toml.minisig"},
}

BAD_TRUST = {"plugin": {"name": "x", "version": "1", "category": "other"},
             "trust": "popular"}
HW_MISSING = {"plugin": {"name": "x", "version": "1", "category": "hardware"},
              "trust": "core"}
MCP_NO_SERVER = {"plugin": {"name": "x", "version": "1", "category": "other"},
                 "trust": "core", "mcp": {"enabled": True}}
# wq#4056 negatives: a truncated/non-hex pin, and provenance missing its commit
# (a release archive that names no commit is exactly the fake-REA-6.3.0 shape).
BAD_HASH = {"plugin": {"name": "x", "version": "1", "category": "other"},
            "trust": "community", "integrity": {"sha256": "9f" * 31 + "ZZ"}}
PROV_NO_COMMIT = {"plugin": {"name": "x", "version": "1", "category": "other"},
                  "trust": "community",
                  "provenance": {"source": "https://github.com/example/x",
                                 "build": "release-archive"}}
BAD_SIGNER = {"plugin": {"name": "x", "version": "1", "category": "other"},
              "trust": "community",
              "signature": {"scheme": "minisign", "key_id": "k",
                            "signer": "friend", "file": "m.minisig"}}


def main():
    schema = json.load(open(SCHEMA_PATH))
    from jsonschema import Draft202012Validator
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    failures = 0
    for name, doc in (("ghidra", GHIDRA), ("magewell", MAGEWELL),
                      ("signed_trust_gated", SIGNED)):
        errs = list(validator.iter_errors(doc))
        ok = not errs
        print(name, "VALID" if ok else "INVALID: " + errs[0].message)
        failures += 0 if ok else 1

    for name, doc in (("bad_trust", BAD_TRUST),
                      ("hardware_missing_block", HW_MISSING),
                      ("mcp_enabled_no_server", MCP_NO_SERVER),
                      ("bad_integrity_hash", BAD_HASH),
                      ("provenance_no_commit", PROV_NO_COMMIT),
                      ("bad_signer", BAD_SIGNER)):
        errs = list(validator.iter_errors(doc))
        rejected = bool(errs)
        print(name, "REJECTED(good)" if rejected else "ACCEPTED(BUG)")
        failures += 0 if rejected else 1

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
