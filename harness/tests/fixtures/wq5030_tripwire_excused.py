"""wq#5030: pinned values for two more TRIPWIRE_CORPUS_EXCUSED rows in test_masking.py
(test_the_mask_suite_does_not_arm_its_own_tripwire).

Both files hold a credential-shaped string BY DESIGN, so splitting it (as every other committed
fixture does, see KV_LINE and fixtures/wq2687_values.py) would change what they test. Neither
value is a credential; each is pinned here by value_sha256_12 and shape so a second, different
shape in the same file still fires (the excuse is by exact (label, prefix, value_len), never
by bare path).

ops/wq4945-mcp-name-collisions/fixtures/mcp-tool-pins-2026-11.json
    A copy of the security-gate tool-pin baseline. The values are 64-hex sha256 pins of MCP
    tool descriptions; the kv rule reads them because the TOOL NAME ends in `key`
    (browser_press_key, press_key). The file is a byte-identical copy of the baseline the
    pin check compares against, so it cannot be re-spelled.
      kv-secret  browser_press_key": "  len 64  sha256[:12] d50a122f4dee, 607fa8540934
      kv-secret  press_key": "          len 64  sha256[:12] fc7fad009128

security/agent_injection_bench/cases.json
    The agent-injection benchmark (wq#4927). Its attack payloads are credential-shaped text
    on purpose: the cases prove the guards refuse a forbidden call that arrives wrapped in
    one. The values are made-up strings, not live material.
      kv-secret  ANTHROPIC_API_KEY=     len 15  sha256[:12] dce04ccea8e7
      kv-secret  confirm_token": "      len 26  sha256[:12] a0e7322b8ff3
      kv-secret  confirm_token:         len 27  sha256[:12] 04fd2da7b8c6
"""
