# wq#4056 — Limpet plugin supply-chain trust gate: signing, pinned hashes, provenance

Status: design. Author: build worker, 2026-10-15.
Relates: wq#3810 (plugin lifecycle), wq#3884/wq#3899 (manifest url/license fill),
wq#3961 (v2 hash-pinning sketch, `docs/plugin-spec.md` §6.3), wq#4014 (content
sanitization — the *other* half of plugin trust), wq#3806 (`registry.lock.json` sketch).

## 1. Why now — the REA trojan

A trojaned fake copy of REA (`morluto/rea`) circulated as a bogus "release 6.3.0" —
an 82.6 MB archive downloaded **172 times** before being flagged (pasqualepillitteri
fact-check, 2026-10-14; surfaced by the 2026-10-15 live-search currency pass). This is a
supply-chain attack on the RE tool itself, not its marketplace: the lure was a release
artifact that no commit in the real repo produced.

Limpet's plugin system today covers discovery and metadata, not payload integrity:

- wq#3810 validates the *manifest* fail-closed (`limpet/manifest.py:249`,
  `limpet/registry.py:80`) but never looks at the plugin payload the manifest fronts.
- wq#3884/wq#3899 added `url`/`license`/`maintainer` and `[source] git/rev`
  (`limpet/manifest.py:94-136`) — provenance *claims*, verified by nobody.
- wq#3998 added `[source].canonical` anti-impersonation (`limpet/manifest.py:129`),
  which catches name-squatting but not a tampered payload at the *right* URL.
- `docs/plugin-spec.md` §5 records the trust/signing model as "**Today: none**" and
  §6.3 already sketches hash pinning as v2 work. This doc is that design, promoted to
  a concrete gate by real-world evidence.

Each of the three mechanisms below stops a distinct attack the REA incident composes:

| Attack | Stopped by |
|---|---|
| Tampered payload at the real source (force-push, rewritten tag, MITM'd fetch) | pinned content hash |
| Impersonated maintainer / squatted name shipping a "new release" | signature + pinned key |
| Release artifact that no repo commit produced (the 82.6 MB fake 6.3.0) | provenance (repo + commit + build origin) |

Corroborating field evidence already in the corpus: 42,000+ exposed OpenClaw instances
plus malicious skills on the ClawHub marketplace — popular agent + unvetted registry =
mass compromise (`design/wq3929_competitive_analysis.md:452-455`); and GhidraMCP-family
servers shipping on 127.0.0.1 with no auth — network position is not an identity, so the
gate must live in the registry/loader, never in "it came from localhost".

## 2. Threat model and non-goals

In scope: tampered plugin payloads, impersonated publishers, fake releases,
post-install on-disk modification (malware or operator error), rollback to a
known-vulnerable rev. Out of scope here: a *malicious but correctly signed* plugin
(that is the trust-tier review's job, `TOOL_REGISTRY.md` go/no-go), runtime sandbox
escape (wq#3961 §6.2 broker design), and hostile *content a plugin analyzes* (wq#4014).

Constraint that shapes everything: Limpet is an **offline-capable appliance**. The
design cannot depend on an online transparency log, OIDC identity (sigstore/cosign's
keyless mode), or a live revocation endpoint. Prior art applied accordingly:
**minisign-style Ed25519 detached signatures** (small, offline, stdlib-verifiable),
**TUF**'s lessons on key compromise survivability taken as "separate roles, pin keys,
plan rotation" without adopting full TUF metadata, and **SLSA provenance** reduced to a
three-field predicate. The ten-ecosystem survey (wq#3932 §2) found *no* RE-tool peer
signs plugins at all — this is the "one place Limpet can lead" (`docs/plugin-spec.md:117`).

## 3. Manifest schema delta (on the wq#3899 format)

Three new optional TOML tables, additive to `limpet-plugin.toml` exactly as
`[optional_dependencies]` was in wq#3945 — no rewrite, v1 manifests stay valid.
Formalized in `design/limpet/plugin-manifest.schema.json` (`integrity`, `provenance`,
`signature` properties) with positive/negative cases in
`design/limpet/test_plugin_manifest_schema.py`.

```toml
[integrity]
algo   = "sha256"                 # only member of the initial allow-set
sha256 = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
# Content hash of the plugin payload tree (see §4 for the exact tree-hash rule).

[provenance]
source     = "https://github.com/example/limpet-ghidra-mcp"  # must equal [source].canonical when both present
commit     = "4a7c1f0e2b9d8c3a5e6f7a8b9c0d1e2f3a4b5c6d"      # 40-hex git commit the payload was built from
build      = "git-clone"          # git-clone | release-archive | local-build
builder    = "limpet-ci"          # who/what produced the artifact (free-form id)
fetched_at = "2026-10-15"

[signature]
scheme  = "minisign"              # Ed25519 detached signature
key_id  = "RWQf6LRCGA9i5..."      # public key id, pinned via the index (§6)
signer  = "maintainer"            # maintainer | org
file    = "limpet-plugin.toml.minisig"   # detached sig, relative to plugin root
```

What is signed: the **manifest file bytes** (which include `[integrity].sha256`), so
the signature covers the payload transitively — one detached sig authenticates both
the metadata and, through the pinned hash, every payload byte. The signature file
itself and `.git/` are excluded from the tree hash (a file cannot contain its own hash).

Mirror fields on `PluginManifest` (`limpet/manifest.py:94-136`): `integrity: dict`,
`provenance: dict`, `signature: dict`, each `{}` = not declared, following the
existing "undeclared is unknown, not safe" convention of `mutation`
(`limpet/manifest.py:119-122`). `validate_manifest` (`limpet/manifest.py:249`) gains
fail-closed shape checks: `sha256` must be 64 lowercase hex, `commit` 40 hex, `build`
in the enum, and `[provenance].source != [source].canonical` (when both present) is a
**validation reject**, not a warning — a manifest that disagrees with itself about its
own origin is exactly the REA-fake shape.

## 4. Verification algorithm

**Tree hash rule (deterministic, Nix-NAR/go.sum-shaped):** walk the plugin root;
exclude `.git/` and `[signature].file`; sort relative paths bytewise; per file hash
`sha256(path + "\0" + mode_class + "\0" + contents)` where `mode_class` is `x` or `-`
(executability matters for payloads; other mode bits don't survive git anyway);
tree hash = sha256 of the concatenated per-file hashes. Symlinks hash their target
*string*, never the target file — the wq#3984 tarfile-symlink-escape lesson
(ISSUE-LOG row 1344) applies to hashing walks too.

**At install** (registry scan admitting a new/changed drop-in, `limpet/registry.py:80`):

1. Parse + validate manifest (existing path).
2. If `[signature]` present: verify the detached sig over the manifest bytes against
   the key pinned for this plugin name (§6). Bad sig → **`tampered`**.
3. If `[integrity]` present: compute tree hash of the payload root; mismatch →
   **`tampered`**.
4. Record the outcome in the lock (§5) with a timestamp.

**At load** (every reconcile before a plugin may resolve `active`,
`limpet/daemon.py` → `registry_core.resolve`): re-check the on-disk tree hash against
the **lock entry** (not the manifest — the manifest is on-disk too and tampering
rewrites both together; only the lock, written by the registry on admit, survives).
Mismatch → `tampered`. This is the `registry.lock.json` fail-closed behavior
`design/wq3806_limpet_architecture.md:86-88` sketched, finally specified.

**Verification states** (computed per plugin, carried in `unified.json`):

| state | meaning | loadable? |
|---|---|---|
| `verified` | sig valid against pinned key, hash matches, provenance present & self-consistent | yes |
| `pinned` | hash matches; no signature (or key not yet pinned) | opt-in only |
| `unverified` | no `[integrity]` and no `[signature]` — a bare wq#3810-era manifest | opt-in only |
| `tampered` | sig **or** hash **mismatch** | never — no opt-in exists |

`tampered` is absolute: absence of evidence downgrades, *contradicting* evidence
rejects. There is no flag, config key, or tier that loads a `tampered` plugin.

**Effective-state ladder delta** (`limpet/registry_core.py:137-153`): one new rung,
`unverified`, slotted after `blocked` and before `unhealthy` — you learn a plugin is
inert for trust reasons before you waste a health probe on it. Ladder becomes:
`disabled` → `cycle` → `blocked` → **`unverified`** (verification ∈ {pinned,
unverified} without opt-in, or `tampered` always, with reason text naming which) →
`unhealthy` → `active`. Resolution stays pure: scan passes a per-plugin
`verification` map into `resolve()` exactly as `health_ok` is passed today
(`limpet/registry_core.py:97`).

**Trust-tier interaction** (`design/limpet/plugin-manifest.schema.json` `trust`):
`core` and `vetted` **require** `verified` — the opt-in below does not exist at those
tiers; a vetted plugin that stops verifying drops to inert, full stop. The opt-in
escape hatch is for `community` only. Promotion to vetted (TOOL_REGISTRY go/no-go)
now *includes* org-key signing (§6), so "vetted" and "verified" converge.

## 5. Lockfile

`/var/lib/limpet/plugins.lock.json` (same directory and atomic-rename discipline as
`unified.json`, `limpet/registry.py:41`), one entry per admitted plugin:

```json
{"ghidra-mcp": {"version": "0.3.1", "rev": "v0.3.1",
  "tree_sha256": "9f86...", "key_id": "RWQf6...", "signer": "maintainer",
  "verification": "verified", "admitted_at": "2026-10-15T14:02:11Z",
  "provenance": {"source": "https://github.com/example/limpet-ghidra-mcp",
                  "commit": "4a7c1f0e...", "build": "git-clone"}}}
```

The lock is the registry's memory of what it admitted: load-time checks compare disk
against lock, update flows compare *new* manifest against *old* lock entry (a version
that goes backwards, or a key_id that changes without an explicit
`limpet trust rotate`, is refused — rollback and key-swap protection, the TUF lesson).
Rollback to a previous good state is "restore the previous lock entry and re-fetch",
which survives a force-pushed upstream branch (`docs/plugin-spec.md:256-258`).

## 6. Keys: who signs, how keys are trusted

- **Org key** (`signer = "org"`): one Lumen-held minisign keypair, private half kept
  as a 0600 file on the build box, never in a container (instinct: no master key in a
  public-facing container). Signs every `core`/`vetted` plugin at promotion time; the
  public key ships baked into the Limpet image, so core/vetted verification works
  fully offline out of the box.
- **Maintainer keys** (`signer = "maintainer"`): the community index entry
  (`docs/plugin-spec.md` §8 step 2 already reserves "publisher key id") carries the
  maintainer's public key; the index CI gate verifies the submitted rev's signature
  before merge. On the appliance, the first install of a plugin pins
  `(name → key_id)` in the lock — **trust-on-first-use, pin thereafter**
  (wq#3932 §3.2) — so a later "release" signed by a different key is refused even if
  the index was not refreshed.
- **Rotation**: `limpet trust rotate <name> <new-key-id>` — explicit, logged,
  requires the new key to be present in a refreshed index entry. Never automatic.
- **Revocation**: the index repo carries a `revoked.json` (key ids + known-bad revs —
  the fake REA 6.3.0 would be a row here). `limpet index update` refreshes it;
  offline staleness **warns** with the age of the revocation list but does not brick
  the appliance (fail-closed on contradiction, fail-loud on staleness).

Deliberately *not* sigstore/cosign keyless: that trades offline operation for OIDC +
a transparency log, wrong for an appliance. Recorded as future work if a hosted
Limpet index ever wants Rekor-style public auditability.

## 7. The visible unverified-source warning

Silence is the failure mode — 172 people downloaded the fake REA because nothing
*in the tool* said "this artifact matches no known release". Every surface shows
verification state:

**CLI** (`limpet/cli.py`):
- `limpet list` gains a verification column; `pinned`/`unverified` render with a
  leading `!` and the word spelled out (`! UNVERIFIED — no signature, no pinned
  hash`), `tampered` renders as `!! TAMPERED — refusing to load` in red when tty.
- `limpet enable <name>` on a non-`verified` community plugin **refuses** (exit 3)
  and prints a multi-line warning naming what is missing, the REA incident as the
  why, and the exact opt-in invocation. The opt-in is
  `limpet enable <name> --allow-unverified`, which persists
  `{"<name>": {"enabled": true, "allow_unverified": true}}` in `enabled.json`
  (`limpet/registry.py:128`) — per-plugin, durable, auditable, and revoked by plain
  `limpet disable`. No global "allow all unverified" setting exists.
- `limpet verify [<name>]` runs the §4 checks on demand and exits non-zero on any
  non-`verified` plugin, so CI and the critic can gate on it.
- `tampered` has no flag. The only path is delete-and-reinstall from a good source.

**GUI** (agent-chat, `docs/agent-chat-spec.md`; desktop, `docs/desktop-spec.md`):
reads `verification` from `unified.json` — no second implementation of the policy.
A persistent amber banner while any *active* plugin is non-`verified` ("Plugin
ghidra-mcp is running unverified — no signature or pinned hash. [Details]"), a
blocking confirm dialog on enable-from-GUI that restates the CLI warning, and
`tampered` shown as an error row with no enable control at all. The banner does not
time out and cannot be dismissed-forever — dismiss hides it for the session only.

**Agent**: the MCP meta-server already refuses to proxy `community` tool calls
(`design/wq3806_limpet_architecture.md:80-84`); it additionally refuses any
non-`verified` plugin's calls unless the operator opt-in is present, returning a
structured `unverified_plugin` error the agent can surface in chat — the agent must
never be the one deciding to run unverified code.

## 8. Rollout

1. **Phase 1 (mechanism):** manifest fields + validator + tree hash + lock +
   `unverified` ladder rung + CLI warnings. Everything is warn-and-opt-in; nothing
   existing breaks (all current example manifests are `unverified`, visible as such).
2. **Phase 2 (keys):** org keypair minted, core/vetted examples signed at promotion,
   `core`/`vetted` begin *requiring* `verified`.
3. **Phase 3 (index):** community index repo (`docs/plugin-spec.md` §3/§8) carries
   publisher keys + `revoked.json`; CI gate verifies signatures pre-merge;
   `limpet install <name>` checks the index entry's pinned hash before the payload
   ever touches the drop-in dir.

Future work: full TUF metadata if the index grows multiple maintainers of the index
itself; sigstore/Rekor for public auditability; per-file hash manifests for partial
verification of very large payloads (Ghidra-sized trees).
