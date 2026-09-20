# wq#2059 — per-operator approval keys for the phase2 executor two-person rule

Fixes SECURITY_AUDIT.md H3 (wq#2050): `auth.py:102-107` counts `approvers` from a
claim inside a JWT signed by ONE key (`LUMEN_APPROVAL_KEY`). Whoever holds that
single private key can list any two roster names, so "dual authorization" is a
claim, not a control.

## Current state (evidence, 2026-09-20)

- `lumen_executor/auth.py:76-77` — token verified against the single pubkey at
  `LUMEN_APPROVAL_KEY` (`/etc/lumen/keys/operator_pub.pem`).
- `lumen_executor/auth.py:102-112` — `approvers` is a free-text list claim;
  checks are `len(set(approvers)) >= required_approvers` and
  `set(approvers) ⊆ roster`. No cryptographic binding of any name to a key.
- `/etc/lumen/operator_roster.json` = `{"operators": ["frank"]}`. With a
  one-name roster, `len(set(approvers)) >= 2` and `⊆ {frank}` cannot both hold,
  so `risk: high` is currently **unreachable**. That is the safe state; it MUST
  be preserved until this design ships.
- `lumen_executor/crypto.py:10-44` — detached Ed25519 verification primitive
  already exists (used for skills/manifests).
- `tools/lumen_sign.py:2` docstring lists a `gen-token` command that was never
  implemented (`cmd_` functions at lines 16/40/43/46 are keygen, sign-skill,
  sign-manifest, gen-manifest only). There is no token-minting tool to migrate;
  approval tooling is greenfield.

## Design

### 1. Roster v2: name → pubkey

`/etc/lumen/operator_roster.json` becomes:

```json
{
  "version": 2,
  "operators": {
    "frank": {
      "pubkey_pem": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n",
      "added": "2026-09-20"
    }
  }
}
```

- Pubkeys are embedded PEM, not file paths — avoids a second path-TOCTOU of the
  M4 variety and makes the roster file the single reviewable artifact.
- File stays root-owned `0644`; private keys are generated on each operator's
  own machine (`lumen_sign.py keygen`) and never touch Lumen.
- Version detection: a v1 list-of-names roster loaded while
  `required_approvers >= 1` on the new code path → `SecurityAbort` (fail
  closed; forces the migration to be explicit).

### 2. Approval bundle: one canonical payload, N detached signatures

Replace the single JWT with an approval bundle file:

```json
{
  "payload_b64": "<base64(canonical-JSON payload bytes)>",
  "signatures": [
    {"operator": "frank",  "sig_b64": "<base64(Ed25519 sig over payload bytes)>"},
    {"operator": "second", "sig_b64": "..."}
  ]
}
```

Payload (canonical JSON: sorted keys, no whitespace, UTF-8) carries exactly the
claims the JWT carries today: `asset_id`, `skill_id`, `skill_sha256`, `iat`,
`exp`, `jti`, `aud` (`lumen-edge-agent`), `iss` (`lumen-hub-walnut-creek`),
plus `risk`. Every signer signs the **same payload bytes**; signatures are
detached, so approvals can be gathered asynchronously (operator 1 mints the
payload, operator 2 co-signs the identical bytes).

### 3. Verification (replaces `verify_approval_token`)

1. Decode `payload_b64`; re-serialize canonically and require byte-equality
   (rejects malleable duplicates with distinct `jti`-free encodings).
2. Standard claim checks exactly as today (`auth.py:95-100` equivalents:
   asset/skill/hash match, exp/iat with the same 10s leeway, aud/iss).
3. For each signature entry: look up `operator` in roster v2 → pubkey;
   unknown name → `SecurityAbort`. Verify the Ed25519 sig over the payload
   bytes via the `crypto.py` primitive.
4. Count **distinct operator names with a valid signature**. Invalid or
   duplicate signatures never count; any invalid signature is also audit-logged
   (attempted forgery is a signal, not noise).
5. `distinct_valid >= required_approvers` or `SecurityAbort`.
6. `consume_jti(payload["jti"], payload["exp"])` once per bundle, as today
   (`auth.py:114`) — one jti covers the whole bundle so a co-signature cannot
   be replayed onto a second run.
7. New explicit guard: `required_approvers <= number of distinct roster keys`,
   else `SecurityAbort("roster too small for required approvers")` — makes the
   currently-implicit "high unreachable with one operator" invariant explicit
   and keeps it true even if a future refactor loosens the subset check.

### 4. Tooling (`tools/lumen_sign.py`)

- `keygen` — unchanged; each operator runs it locally.
- `gen-approval --asset --skill --skill-file --risk --exp --key frank.key`
  → writes `approval.json` with payload + first signature.
- `co-sign approval.json --key second.key --operator second`
  → verifies the existing payload, appends a signature. Refuses to sign if the
  payload's `skill_sha256` doesn't match a locally supplied `--skill-file`
  (each approver independently hashes what they're approving).
- `verify-approval approval.json --roster` — offline pre-flight check.

### 5. Migration and rollout

1. Ship roster v2 + bundle verification behind the existing `_cfg` paths
   (`LUMEN_APPROVAL_KEY` retired; `LUMEN_OPERATOR_ROSTER` reused).
2. `risk: medium` (1 approver) moves to the same bundle path — one valid
   per-operator signature. No JWT back-compat window: nothing in production
   mints JWTs today (no `gen-token` exists), so dual-path support is pure
   attack surface.
3. `risk: high` stays unreachable until a second operator's key lands in
   roster v2 by Frank's explicit roster edit (root-gated file on Lumen).
4. Optional hardening, out of scope here: sign the roster itself with an
   offline root key and verify via `crypto.py` before use.

### 6. Test plan

- Unit: valid 2-of-2 bundle passes; same-operator double-signature counts as 1;
  unknown operator aborts; tampered payload byte aborts; sig from a key not in
  roster aborts; jti replay of a fully-signed bundle aborts; v1 roster aborts;
  `required_approvers > roster size` aborts.
- Regression: with roster = frank only, every `risk: high` path raises
  `SecurityAbort` (pins the safe state this doc requires).

## Interim invariant (until implemented)

`/etc/lumen/operator_roster.json` stays `{"operators": ["frank"]}`. Any PR that
adds a second name to a v1 roster re-opens H3 verbatim and must be rejected.
