# Skill Supply-Chain Defense: Admission Pipeline

**wq#4924** | Part of 4-project security validation  
**Status**: Implemented  
**Scanner**: `security/skill-admission/scan_skill.py`  
**Pipeline**: `security/skill-admission/admit_skill.py`

## Threat Model

Attack vectors documented by Snyk ToxicSkills (Feb 2026, https://snyk.io/blog/toxicskills-malicious-ai-agent-skills-clawhub/), OWASP AST-01 (https://owasp.org/www-project-agentic-skills-top-10/ast01), and Unit 42 (https://unit42.paloaltonetworks.com/openclaw-ai-supply-chain-risk/):

| Vector | Prevalence | Description |
|---|---|---|
| Credential exfiltration | 100% of malicious skills | Reads ~/.ssh, wallets, browser data, .env files |
| Prompt injection | 36% of all ClawHub skills | Markdown prose overrides agent instructions |
| Reverse shell / C2 | Common in ClawHavoc campaign | bash /dev/tcp, netcat -e, socket.connect |
| Obfuscated payloads | 91% combine with prompt injection | base64, eval, exec, char code construction |
| Shadow tool registration | USENIX Security 2026: 73.2% | Hidden tool definitions in SKILL.md prose |
| Dependency confusion | Ongoing | Typosquatted packages in requirements/package.json |
| Excessive capabilities | Standard evasion | Declared permissions vs actual code capability mismatch |

Key statistics from ToxicSkills research:
- 3,984 ClawHub skills analyzed, 1,467 malicious payloads found
- ClawHavoc campaign: 1,184 malicious skills across 12 publisher accounts sharing C2 IP 91.92.242.30
- Three lines of SKILL.md were sufficient to exfiltrate SSH keys
- 100% of malicious skills combined code-layer AND instruction-layer attacks

## Pipeline Stages

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  Quarantine  │───▶│   Record    │───▶│    Scan     │───▶│   Approve   │───▶│   Promote   │
│              │    │             │    │             │    │             │    │             │
│ Copy to      │    │ Publisher   │    │ Static      │    │ Evidence-   │    │ Immutable   │
│ isolated     │    │ Repo/SHA    │    │ analysis    │    │ backed      │    │ versioned   │
│ staging      │    │ Release     │    │ 8 scanner   │    │ human       │    │ copy to     │
│              │    │ Manifest    │    │ categories  │    │ review      │    │ registry    │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
     FAIL at any stage ──▶ REJECT (record preserved for forensics)
```

### Stage 1: Quarantine
```bash
python3 security/skill-admission/admit_skill.py quarantine /path/to/skill \
  --source clawhub --publisher user/repo --release v1.0.0
```
- Copies skill to `$SKILL_PIPELINE_ROOT/quarantine/<id>/skill/`
- Computes SHA-256 manifest of every file
- Creates admission record in `$SKILL_PIPELINE_ROOT/records/`
- Status: `quarantined`

### Stage 2: Scan
```bash
python3 security/skill-admission/admit_skill.py scan <quarantine-id> [--strict]
```
Scanner categories (8 total):
1. **credential-access** — references to ~/.ssh, .env, wallets, browser credential stores
2. **exfiltration** — curl POST, wget --post, netcat -e, reverse shells, requests.post
3. **obfuscation** — eval/exec, base64 decode, hex/unicode encoding, os.system
4. **prompt-injection** — instruction overrides, role hijacking, fake system messages, special tokens
5. **shadow-tool** — hidden tool definitions in markdown, dynamic tool registration
6. **suspicious-url** — ngrok, webhook.site, requestbin, hard-coded public IPs
7. **capability-mismatch** — undeclared shell/filesystem/network/database access
8. **typosquat-dependency** — known typosquat prefixes in requirements/package.json

Verdicts: PASS (no HIGH+), REVIEW (HIGH but no CRITICAL), FAIL (any CRITICAL)  
`--strict`: FAIL on any HIGH finding

### Stage 3: Approve
```bash
python3 security/skill-admission/admit_skill.py approve <quarantine-id> \
  --reviewer "name" --notes "Reviewed: findings are false positives because..."
```
- Requires status = `scanned` and verdict != FAIL
- Records reviewer identity, timestamp, and notes
- Human accountability — no auto-approve path

### Stage 4: Promote
```bash
python3 security/skill-admission/admit_skill.py promote <quarantine-id>
```
- Copies to `$SKILL_PIPELINE_ROOT/registry/<skill-name>/<quarantine-id>/`
- Writes immutable MANIFEST.json with full provenance chain
- Registry copy is the production artifact

### Bulk Audit (existing skills)
```bash
python3 security/skill-admission/admit_skill.py audit-existing /path/to/skills [--json] [--strict]
```
Scans all subdirectories, prints per-skill verdict + summary.

## Scanner Detection Coverage

| Attack Pattern | Scanner Rule | Severity |
|---|---|---|
| `~/.ssh/id_rsa` read | credential-access | CRITICAL |
| `curl -X POST` to external | exfiltration | CRITICAL |
| `bash -i >& /dev/tcp/` | exfiltration | CRITICAL |
| `eval(base64.b64decode(...))` | obfuscation | HIGH |
| `os.system("curl ...")` | obfuscation + exfiltration | HIGH + CRITICAL |
| "ignore previous instructions" | prompt-injection | CRITICAL |
| `<\|im_start\|>system` | prompt-injection | CRITICAL |
| "send the contents to https://" | prompt-injection | CRITICAL |
| Hidden `register_tool()` call | shadow-tool | HIGH |
| `ngrok.io` / `webhook.site` URL | suspicious-url | HIGH |
| npm `postinstall: "curl ..."` | malicious-script | CRITICAL |
| `googl-search` package name | typosquat-dependency | HIGH |
| Uses subprocess but declares no shell | capability-mismatch | HIGH |

## Filesystem Layout

```
$SKILL_PIPELINE_ROOT/           (default: /home/lumen/foreman/security/skill-pipeline)
├── quarantine/                 Isolated staging area
│   └── q-<name>-<ts>-<hash>/
│       └── skill/              Verbatim copy of skill files
├── registry/                   Promoted immutable copies
│   └── <skill-name>/
│       └── q-<name>-<ts>-<hash>/
│           ├── skill/          Production artifact
│           └── MANIFEST.json   Full provenance chain
├── records/                    Admission records
│   ├── q-<name>-<ts>-<hash>.json       Status + metadata
│   └── q-<name>-<ts>-<hash>.scan.json  Full scan results
└── audit.jsonl                 Append-only audit trail
```

## Integration Points

- **Existing 75 skills**: Run `audit-existing` against skill roots on first deploy
- **Future ClawHub acquisitions**: Quarantine before any execution; reject on CRITICAL
- **CI gate**: `scan_skill.py --json --strict` returns exit 1 on any HIGH+ finding
- **MCP-Scan coordination**: Scanner output format compatible with MCP-Scan findings merge
- **MCP fuzzer**: Scan results feed into fuzzer's target prioritization
- **Injection benchmark**: Prompt injection patterns shared with benchmark test corpus

## Audit Trail

Every pipeline action appends to `audit.jsonl` (NDJSON):
```json
{"event":"quarantine","quarantine_id":"q-...","skill_name":"...","timestamp":"2026-..."}
{"event":"scan","quarantine_id":"q-...","verdict":"PASS","findings_count":0,"timestamp":"2026-..."}
{"event":"approve","quarantine_id":"q-...","reviewer":"frank","timestamp":"2026-..."}
{"event":"promote","quarantine_id":"q-...","destination":"/home/lumen/...","timestamp":"2026-..."}
```

## Security Considerations

- Quarantine directory is isolated — skills never execute from quarantine
- SHA-256 manifest computed at quarantine, re-verified at promotion
- No auto-approve path — human reviewer required for every skill
- Rejected skills preserved in quarantine for forensic analysis
- Audit log is append-only (append mode file open, no truncation)
- Scanner runs pure static analysis — no skill code is executed during scan
