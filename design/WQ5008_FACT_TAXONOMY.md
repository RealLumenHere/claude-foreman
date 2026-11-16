# wq#5008 — QWED dual-axis verification taxonomy for lumen_state facts

Pattern source: `scratch/research/extract_qwed_verification.md` (wq#4968,
QWED-AI/qwed-verification). Adopted: the result taxonomy, the status/admission split and
`proof_ref`. Not adopted: the QWED server, and its `logic` / `consensus` /
`natural_language` routes.

## Defect class

`lumen_state` printed one word, `UNVERIFIABLE — no probe`, for three different situations:

| situation | what it really is |
|---|---|
| nothing deterministic can check this fact | a coverage gap (fine, and most narrative facts) |
| a probe exists and returned nothing | a **broken probe** — invisible, looked like "no probe" |
| the value states something an engine can decide | checkable today, never checked |

and it had no way to say "confirmed, and still not safe to act on".

## Model

    status     VERIFIED | BLOCKED | UNVERIFIABLE      (does a deterministic verdict exist)
    admission  ADMIT | DENY                           (may the fact drive a decision)

Independent axes. `VERIFIED` carries a `scope`:

- `world` — a registered lumen_verify probe observed the wire and matched
  (`fact_verifications.outcome = confirmed`, or the open row was itself written by a
  probe observation).
- `formal` — an engine proved a claim the value itself makes. A formal proof is not
  evidence about the world (QWED #320), so it never touches lumen_state's evidence clock:
  **this module opens minerva.db read-only and writes nothing to it.**

Status precedence: world VERIFIED > BLOCKED (a failing probe is the operationally
important signal) > formal VERIFIED > UNVERIFIABLE. Admission is computed from every
check regardless of which status won.

`DENY` when any of: `contradicted`, an engine verdict of proven-false
(`arithmetic_mismatch`, `address_out_of_range`, `malformed_address`,
`port_out_of_range`, `lifecycle_outside_enum`, `destructive_command`,
`injection_tautology`, `stacked_queries`, `forbidden_call:*`, …), `stale_evidence`
(world evidence older than 3x the verify interval), `closed_lifecycle` (operational
predicate on a retired/decommissioned subject).

`proof_ref` = SHA-256 over canonical JSON of `{v, fact_id, subject, predicate,
value_sha256, scope, evidence}`. Present **iff** status is VERIFIED — enforced three ways:
the classifier, a `CHECK` constraint on the ledger table, and an exhaustive test.
`verify-proof <fact_id>` re-derives it from live state; a changed value, changed evidence
or an edited ledger row fails.

## Engines

| route | engine | reads | example |
|---|---|---|---|
| math | SymPy | `TOTAL unit = N x EACH unit` (parenthetical form on memory predicates only) | `64GB = 4x16GB` |
| constraint | Z3 | leading IPv4 token on `*_ip` (well-formed; tailscale in 100.64/10, others RFC1918), `port`, `lifecycle` enum | negation unsat = proven |
| sql | SQLGlot | SQL-bearing values (`*_sql`, `*_query`, or a leading SQL keyword) | DROP / DELETE-no-WHERE / `1=1` / stacked |
| code | Python `ast` | Python-bearing values; `ast.literal_eval` for `price` | `os.system`, `eval`, `subprocess(shell=True)` |
| probe | — | `fact_verifications` | existing node_storage / os_kernel / … probes |

A missing engine library is `BLOCKED`, never a pass. z3-solver and sqlglot live in
`/home/lumen/.local/share/qwed-engines/venv` (system python untouched); the module
appends that site dir on demand.

### Routes refused by name

`verify_claim("logic" | "consensus" | "natural_language", …)` raises `RouteRefused`.
QWED #320/#581/#585: the natural-language -> constraint translation is unverified, so a
VERIFIED from those routes proves nothing about the fact. Z3 is used here only on
formulas **built by code from a regex-parsed token** — no prose reaches a solver, and a
test asserts the module imports no HTTP, model or subprocess client.

## Storage and use

Foreman-side ledger `foreman/fact_taxonomy.db` (untracked data, like queue.db):
`fact_taxonomy` (one row per open fact) + `taxonomy_runs` (summary + engine versions).

    python3 harness/fact_taxonomy.py summary
    python3 harness/fact_taxonomy.py scan [--subject S] [--only BLOCKED|DENY|…] [--write] [--json]
    python3 harness/fact_taxonomy.py verify-proof <fact_id>
    python3 harness/fact_taxonomy.py engines

## Not done here (minerva is a separate repo; this ticket is foreman-only)

Surfacing the taxonomy in `lumen_state.py current` / the `--core` VERIFY line, and a
scheduled `scan --write`. Filed as follow-ups.
