"""wq#2806 shape survey: which credential-labelled lines in REAL fleet config files do
masking.py / bin/mask-secrets leave in the clear? Prints shape descriptors + value hashes,
never values (instinct 4)."""
import os, re, sys, subprocess, hashlib, glob, json
sys.path.insert(0, os.environ.get("HARNESS", "/home/lumen/foreman/harness"))
import masking

LABEL = re.compile(r"(?i)(secret|token|passw(or)?d|passphrase|credential|api[_-]?key|access[_-]?key|private[_-]?key|auth[_-]?key|master[_-]?key|encryption[_-]?key|signing[_-]?key|salt|_key\b|_pass\b)")
POINTER = re.compile(r"^(\$\{?|os\.|env\.|<|~?/|\.\.?/)|^(changeme|change_me|example|redacted|masked|xxx+|your[_-]|placeholder|none|null|true|false|\d{1,5})$", re.I)

def sha12(v): return hashlib.sha256(v.encode("utf-8","replace")).hexdigest()[:12]

def sed_mask(text):
    return subprocess.run(os.environ.get("MASK_SECRETS", "/home/lumen/bin/mask-secrets").split(), input=text, text=True, capture_output=True).stdout

def shape(line):
    """(separator, quoting, valuecharset) for a credential-labelled line."""
    s = line.strip()
    m = re.match(r'^(?:-\s*)?(?:export\s+)?["\']?([A-Za-z0-9_.\-\[\]]+)["\']?\s*([=:]|>)\s*(.*)$', s)
    xml = re.match(r'^<([A-Za-z0-9_\-]+)>([^<]+)</\1>', s)
    if xml:
        return "xml", xml.group(1), xml.group(2)
    if not m: return None
    key, sep, rest = m.groups()
    rest = rest.strip()
    q = rest[:1] if rest[:1] in "\"'" else ""
    val = rest
    if q:
        end = rest.find(q, 1)
        val = rest[1:end] if end > 0 else rest[1:]
    else:
        val = re.split(r"\s+#|\s*,\s*$|\s+$", val)[0]
    return ("sep="+sep+(" q"+q if q else " unq")), key, val

def charset(v):
    cls = []
    if re.search(r"[A-Z]", v): cls.append("A")
    if re.search(r"[a-z]", v): cls.append("a")
    if re.search(r"[0-9]", v): cls.append("0")
    others = set(re.sub(r"[A-Za-z0-9]", "", v))
    return "".join(cls) + ("+" + "".join(sorted(others)) if others else "")

def target_files():
    roots = []
    roots += [p for p in glob.glob("/home/lumen/*/docker-compose*.y*ml")+glob.glob("/home/lumen/*/*/docker-compose*.y*ml")]
    roots += [p for p in glob.glob("/home/lumen/.secrets/*.env")+glob.glob("/home/lumen/.secrets/*/*.env")]
    roots += ["/home/lumen/agent/.env","/home/lumen/autonomy/dispatcher/.env","/home/lumen/postgres-mcp/.env",
              "/home/lumen/ha/ha.env","/home/lumen/vaultwarden/vaultwarden.env","/home/lumen/sim-studio-src/.env"]
    roots += glob.glob("/home/lumen/plausible/clickhouse/*.xml")
    roots += glob.glob("/home/lumen/litellm*/*.y*ml")+glob.glob("/home/lumen/*/litellm_config*.y*ml")+glob.glob("/home/lumen/litellm_config*.y*ml")
    roots += glob.glob("/home/lumen/*/config.y*ml")+glob.glob("/home/lumen/*/*.toml")+glob.glob("/home/lumen/*/settings.ini")
    roots += glob.glob("/home/lumen/minerva/context/claude_credentials.md")
    return [r for r in dict.fromkeys(roots) if os.path.isfile(r) and "worktrees" not in r]

def container_xml():
    out = {}
    for c in ("langfuse-clickhouse","plausible_events_db"):
        for f in ("/etc/clickhouse-server/users.d/default-user.xml","/etc/clickhouse-server/users.xml"):
            r = subprocess.run(["docker","exec",c,"cat",f],capture_output=True,text=True)
            if r.returncode==0 and r.stdout.strip():
                out[f"docker:{c}:{f}"] = r.stdout
    return out

def main():
    sources = {p: open(p, errors="replace").read() for p in target_files()}
    sources.update(container_xml())
    rows = []
    for path, text in sources.items():
        for n, line in enumerate(text.splitlines(), 1):
            if not LABEL.search(line): continue
            sh = shape(line)
            if not sh: continue
            kind, key, val = sh
            if len(val) < 8 or POINTER.match(val): continue
            py_masked = "MASKED" in masking.mask_text(line)[0] and val not in masking.mask_text(line)[0]
            sed_out = sed_mask(line+"\n")
            sed_masked = val not in sed_out
            rows.append((path, n, kind, key[-40:], len(val), charset(val), sha12(val), py_masked, sed_masked))
    rows.sort(key=lambda r:(r[7],r[8],r[2]))
    print(f"{'py':3} {'sed':3} {'shape':14} {'len':>3} {'charset':18} sha12        key  path:line")
    for path,n,kind,key,l,cs,h,py,sd in rows:
        print(f"{'Y' if py else '-':3} {'Y' if sd else '-':3} {kind:14} {l:>3} {cs:18} {h} {key}  {path}:{n}")
    miss_py = [r for r in rows if not r[7]]; miss_sed=[r for r in rows if not r[8]]
    print(f"\nlabelled-value lines={len(rows)}  missed_by_masking.py={len(miss_py)}  missed_by_mask-secrets={len(miss_sed)}")
    from collections import Counter
    print("py-miss by shape:", Counter(r[2] for r in miss_py))
    print("sed-miss by shape:", Counter(r[2] for r in miss_sed))
main()
