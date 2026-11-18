#!/usr/bin/env python3
"""Skill admission scanner — static analysis pass for skill supply-chain defense.

Scans a skill directory for indicators of malicious behaviour documented by
Snyk ToxicSkills (Feb 2026), OWASP AST-01, and Unit 42 ClawHub analysis.

Attack surface categories (from ToxicSkills / OWASP AST-01):
  1. Credential exfiltration — reads ~/.ssh, wallets, browser data
  2. Reverse shells / C2 callbacks
  3. Prompt injection in SKILL.md prose
  4. Obfuscated payloads (base64, eval, exec)
  5. Excessive capability requests vs actual use
  6. Suspicious URLs / external fetches
  7. Shadow tool registration (hidden tool definitions in prose)
  8. Dependency confusion / typosquatting

Usage:
  python3 scan_skill.py /path/to/skill [--json] [--strict]
"""

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Finding:
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW, INFO
    category: str
    file: str
    line: int
    description: str
    evidence: str = ""


@dataclass
class ScanResult:
    skill_path: str
    skill_name: str = ""
    publisher: str = ""
    sha256_manifest: dict = field(default_factory=dict)
    findings: list = field(default_factory=list)
    verdict: str = "PENDING"  # PASS, FAIL, REVIEW, PENDING
    file_count: int = 0
    total_lines: int = 0

    def add(self, finding: Finding):
        self.findings.append(finding)

    @property
    def critical_count(self):
        return sum(1 for f in self.findings if f.severity == "CRITICAL")

    @property
    def high_count(self):
        return sum(1 for f in self.findings if f.severity == "HIGH")


CREDENTIAL_PATHS = [
    r"~/\.ssh",
    r"~/\.gnupg",
    r"~/\.aws",
    r"~/\.config/gcloud",
    r"~/\.kube",
    r"id_rsa",
    r"id_ed25519",
    r"authorized_keys",
    r"\.env\b",
    r"credentials\.json",
    r"wallet",
    r"keychain",
    r"\.npmrc",
    r"\.pypirc",
    r"chrome.*Login\s*Data",
    r"firefox.*logins\.json",
    r"brave.*Login\s*Data",
]

EXFIL_PATTERNS = [
    (r"\bcurl\b.*\b(?:POST|PUT|PATCH)\b", "HTTP exfil via curl"),
    (r"\bwget\b.*--post", "HTTP exfil via wget"),
    (r"\bnc\b.*-e\b", "netcat reverse shell"),
    (r"\b(?:bash|sh)\b.*>.*?/dev/tcp/", "bash reverse shell"),
    (r"\bsocket\.connect\b", "raw socket connection"),
    (r"\brequests\.(?:post|put)\b", "HTTP exfil via requests"),
    (r"\bhttpx?\.(?:post|put|patch)\b", "HTTP exfil via httpx"),
    (r"\burllib\.request\.urlopen\b", "URL fetch via urllib"),
    (r"\bsubprocess\.(?:call|run|Popen)\b.*curl", "subprocess curl"),
    (r"fetch\s*\(.*method.*POST", "JS fetch POST"),
    (r"\bXMLHttpRequest\b", "XHR exfil"),
]

OBFUSCATION_PATTERNS = [
    (r"\beval\s*\(", "eval() call"),
    (r"\bexec\s*\(", "exec() call"),
    (r"\bcompile\s*\(", "compile() call"),
    (r"\b__import__\s*\(", "dynamic import"),
    (r"\bimportlib\.import_module\b", "dynamic import via importlib"),
    (r"base64\.(?:b64decode|decodebytes)\b", "base64 decode"),
    (r"atob\s*\(", "JS base64 decode"),
    (r"(?:\\x[0-9a-fA-F]{2}){4,}", "hex-encoded string"),
    (r"(?:\\u[0-9a-fA-F]{4}){3,}", "unicode-encoded string"),
    (r"chr\s*\(\s*\d+\s*\)", "char code construction"),
    (r"String\.fromCharCode", "JS char code construction"),
    (r"\bos\.system\s*\(", "os.system call"),
    (r"\bos\.popen\s*\(", "os.popen call"),
]

PROMPT_INJECTION_PATTERNS = [
    (r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", "instruction override"),
    (r"you\s+(?:are|must)\s+(?:now|actually)", "role hijacking"),
    (r"(?:system|admin)\s*:\s*", "fake system message"),
    (r"<\|(?:im_start|system|endoftext)\|>", "special token injection"),
    (r"do\s+not\s+(?:reveal|tell|show|disclose)", "secrecy instruction"),
    (r"(?:new|updated?)\s+(?:system\s+)?prompt", "prompt replacement"),
    (r"forget\s+(?:everything|all|your)", "memory wipe instruction"),
    (r"execute\s+(?:the\s+)?(?:following|this)\s+(?:command|code|script)", "code execution instruction"),
    (r"send\s+(?:the\s+)?(?:contents?|data|file|key|token|secret|credential)", "exfil instruction"),
    (r"(?:curl|wget|fetch|post)\s+(?:to\s+)?https?://", "URL exfil instruction in prose"),
]

SHADOW_TOOL_PATTERNS = [
    (r"```(?:tool|function|action)\b", "hidden tool definition in code block"),
    (r"register_tool\s*\(", "dynamic tool registration"),
    (r"add_tool\s*\(", "dynamic tool addition"),
    (r"tool_definition\s*=", "tool definition assignment"),
]

SUSPICIOUS_EXTENSIONS = {
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat",
    ".msi", ".dmg", ".app", ".deb", ".rpm",
    ".ps1", ".bat", ".cmd", ".vbs", ".wsf",
    ".war", ".jar", ".class",
}

KNOWN_TYPOSQUAT_PREFIXES = [
    "googl", "gogle", "goggle",
    "openai-", "open-ai",
    "anthropic-", "anthro-",
    "claude-unofficial",
    "gpt-unofficial",
    "chatgpt-",
    "solana-", "ethereum-", "crypto-",
    "polymarket",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def scan_file_for_patterns(filepath: Path, patterns: list[tuple[str, str]],
                            category: str, severity: str,
                            result: ScanResult, binary_ok: bool = False):
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return
    for lineno, line in enumerate(text.splitlines(), 1):
        for pattern, desc in patterns:
            if re.search(pattern, line, re.IGNORECASE):
                result.add(Finding(
                    severity=severity,
                    category=category,
                    file=str(filepath),
                    line=lineno,
                    description=desc,
                    evidence=line.strip()[:200],
                ))


def scan_for_credentials(filepath: Path, result: ScanResult):
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return
    for lineno, line in enumerate(text.splitlines(), 1):
        for pattern in CREDENTIAL_PATHS:
            if re.search(pattern, line, re.IGNORECASE):
                result.add(Finding(
                    severity="CRITICAL",
                    category="credential-access",
                    file=str(filepath),
                    line=lineno,
                    description=f"References credential path: {pattern}",
                    evidence=line.strip()[:200],
                ))


def scan_for_suspicious_urls(filepath: Path, result: ScanResult):
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return
    url_re = re.compile(r"https?://[^\s\"'`\]>)]+", re.IGNORECASE)
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in url_re.finditer(line):
            url = match.group()
            if any(tok in url.lower() for tok in [
                "ngrok", "serveo", "localtunnel", "webhook.site",
                "requestbin", "pipedream", "hookbin", "burpcollaborator",
                "interact.sh", "canarytokens", "dnslog",
            ]):
                result.add(Finding(
                    severity="HIGH",
                    category="suspicious-url",
                    file=str(filepath),
                    line=lineno,
                    description=f"Suspicious URL (exfil/C2 service)",
                    evidence=url[:200],
                ))
            elif re.search(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", url):
                if not url.startswith(("http://localhost", "http://127.0.0.1",
                                       "http://0.0.0.0", "http://192.168.",
                                       "http://10.", "http://172.")):
                    result.add(Finding(
                        severity="MEDIUM",
                        category="suspicious-url",
                        file=str(filepath),
                        line=lineno,
                        description="URL with hard-coded public IP",
                        evidence=url[:200],
                    ))


def scan_capability_mismatch(skill_dir: Path, result: ScanResult):
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        result.add(Finding(
            severity="HIGH",
            category="missing-metadata",
            file=str(skill_dir),
            line=0,
            description="No SKILL.md found — skill cannot declare capabilities",
        ))
        return

    text = skill_md.read_text(encoding="utf-8", errors="replace")

    declared_caps = set()
    cap_re = re.compile(r"(?:capabilities?|permissions?|tools?)\s*:\s*(.+)", re.IGNORECASE)
    for m in cap_re.finditer(text):
        declared_caps.update(re.split(r"[,\s|]+", m.group(1).lower()))

    all_code = []
    for f in skill_dir.rglob("*"):
        if f.suffix in (".py", ".sh", ".js", ".ts") and f.is_file():
            try:
                all_code.append(f.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass
    code_blob = "\n".join(all_code).lower()

    actual_caps = set()
    if re.search(r"(?:subprocess|os\.system|os\.popen|exec\(|child_process)", code_blob):
        actual_caps.add("shell")
    if re.search(r"(?:open|read_file|write_file|pathlib|shutil)", code_blob):
        actual_caps.add("filesystem")
    if re.search(r"(?:requests|httpx|urllib|fetch|curl|wget)", code_blob):
        actual_caps.add("network")
    if re.search(r"(?:sqlite|psycopg|mysql|mongo|redis)", code_blob):
        actual_caps.add("database")

    undeclared = actual_caps - declared_caps
    if undeclared:
        result.add(Finding(
            severity="HIGH",
            category="capability-mismatch",
            file=str(skill_md),
            line=0,
            description=f"Undeclared capabilities used in code: {', '.join(sorted(undeclared))}",
        ))


def scan_dependencies(skill_dir: Path, result: ScanResult):
    dep_files = {
        "requirements.txt": r"^\s*([a-zA-Z0-9_.-]+)",
        "package.json": None,
        "Pipfile": r"^\s*([a-zA-Z0-9_.-]+)\s*=",
        "pyproject.toml": r'^\s*"?([a-zA-Z0-9_.-]+)"?\s*[>=<]',
    }
    for dep_file, pattern in dep_files.items():
        p = skill_dir / dep_file
        if not p.exists():
            continue
        if dep_file == "package.json":
            try:
                pkg = json.loads(p.read_text())
                for section in ("dependencies", "devDependencies"):
                    for name in pkg.get(section, {}):
                        if any(name.startswith(pre) for pre in KNOWN_TYPOSQUAT_PREFIXES):
                            result.add(Finding(
                                severity="HIGH",
                                category="typosquat-dependency",
                                file=str(p),
                                line=0,
                                description=f"Possibly typosquatted package: {name}",
                            ))
                for script_name, script_cmd in pkg.get("scripts", {}).items():
                    if any(tok in script_cmd for tok in ["curl ", "wget ", "nc ", "/dev/tcp"]):
                        result.add(Finding(
                            severity="CRITICAL",
                            category="malicious-script",
                            file=str(p),
                            line=0,
                            description=f"npm script '{script_name}' has suspicious command",
                            evidence=script_cmd[:200],
                        ))
            except (json.JSONDecodeError, KeyError):
                pass
        elif pattern:
            try:
                text = p.read_text()
                for lineno, line in enumerate(text.splitlines(), 1):
                    m = re.match(pattern, line)
                    if m:
                        name = m.group(1).lower()
                        if any(name.startswith(pre) for pre in KNOWN_TYPOSQUAT_PREFIXES):
                            result.add(Finding(
                                severity="HIGH",
                                category="typosquat-dependency",
                                file=str(p),
                                line=lineno,
                                description=f"Possibly typosquatted package: {name}",
                            ))
            except Exception:
                pass


def scan_suspicious_files(skill_dir: Path, result: ScanResult):
    for f in skill_dir.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix.lower() in SUSPICIOUS_EXTENSIONS:
            result.add(Finding(
                severity="HIGH",
                category="suspicious-file",
                file=str(f),
                line=0,
                description=f"Binary/executable file: {f.suffix}",
            ))
        if f.name.startswith(".") and f.suffix in (".py", ".sh", ".js"):
            result.add(Finding(
                severity="MEDIUM",
                category="hidden-script",
                file=str(f),
                line=0,
                description=f"Hidden script file: {f.name}",
            ))


def compute_manifest(skill_dir: Path) -> dict:
    manifest = {}
    for f in sorted(skill_dir.rglob("*")):
        if f.is_file():
            rel = str(f.relative_to(skill_dir))
            manifest[rel] = sha256_file(f)
    return manifest


def scan_skill(skill_dir: Path, strict: bool = False) -> ScanResult:
    result = ScanResult(skill_path=str(skill_dir))

    if not skill_dir.is_dir():
        result.verdict = "FAIL"
        result.add(Finding(
            severity="CRITICAL",
            category="invalid-input",
            file=str(skill_dir),
            line=0,
            description="Skill path is not a directory",
        ))
        return result

    result.skill_name = skill_dir.name
    result.sha256_manifest = compute_manifest(skill_dir)
    result.file_count = len(result.sha256_manifest)

    total_lines = 0
    for f in skill_dir.rglob("*"):
        if f.is_file() and f.suffix in (".py", ".sh", ".js", ".ts", ".md", ".yaml", ".yml", ".json", ".toml"):
            try:
                total_lines += len(f.read_text(errors="replace").splitlines())
            except Exception:
                pass
    result.total_lines = total_lines

    scannable = []
    for f in skill_dir.rglob("*"):
        if f.is_file() and f.suffix in (".py", ".sh", ".js", ".ts", ".md", ".yaml", ".yml", ".json", ".toml", ".txt", ".cfg", ".ini"):
            scannable.append(f)

    for f in scannable:
        scan_for_credentials(f, result)
        scan_file_for_patterns(f, EXFIL_PATTERNS, "exfiltration", "CRITICAL", result)
        scan_file_for_patterns(f, OBFUSCATION_PATTERNS, "obfuscation", "HIGH", result)
        scan_for_suspicious_urls(f, result)
        if f.suffix == ".md":
            scan_file_for_patterns(f, PROMPT_INJECTION_PATTERNS, "prompt-injection", "CRITICAL", result)
            scan_file_for_patterns(f, SHADOW_TOOL_PATTERNS, "shadow-tool", "HIGH", result)

    scan_capability_mismatch(skill_dir, result)
    scan_dependencies(skill_dir, result)
    scan_suspicious_files(skill_dir, result)

    if result.critical_count > 0:
        result.verdict = "FAIL"
    elif result.high_count > 0:
        result.verdict = "REVIEW" if not strict else "FAIL"
    else:
        result.verdict = "PASS"

    return result


def format_text(result: ScanResult) -> str:
    lines = [
        f"=== Skill Admission Scan: {result.skill_name} ===",
        f"Path: {result.skill_path}",
        f"Files: {result.file_count}  Lines: {result.total_lines}",
        f"Verdict: {result.verdict}",
        f"Findings: {len(result.findings)} "
        f"(CRITICAL={result.critical_count}, HIGH={result.high_count})",
        "",
    ]
    for i, f in enumerate(result.findings, 1):
        lines.append(f"[{i}] {f.severity} | {f.category}")
        lines.append(f"    {f.file}:{f.line}")
        lines.append(f"    {f.description}")
        if f.evidence:
            lines.append(f"    evidence: {f.evidence}")
        lines.append("")
    if result.sha256_manifest:
        lines.append("--- File manifest (SHA-256) ---")
        for rel, h in sorted(result.sha256_manifest.items()):
            lines.append(f"  {h[:16]}… {rel}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Skill admission scanner")
    parser.add_argument("skill_dir", help="Path to skill directory")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--strict", action="store_true",
                        help="Fail on HIGH findings (default: REVIEW)")
    args = parser.parse_args()

    skill_dir = Path(args.skill_dir).resolve()
    result = scan_skill(skill_dir, strict=args.strict)

    if args.json:
        out = {
            "skill_path": result.skill_path,
            "skill_name": result.skill_name,
            "publisher": result.publisher,
            "verdict": result.verdict,
            "file_count": result.file_count,
            "total_lines": result.total_lines,
            "findings": [asdict(f) for f in result.findings],
            "sha256_manifest": result.sha256_manifest,
        }
        print(json.dumps(out, indent=2))
    else:
        print(format_text(result))

    sys.exit(0 if result.verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
