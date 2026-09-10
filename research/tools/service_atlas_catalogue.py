#!/usr/bin/env python3
"""wq#1627 — generate the mechanical unit catalogue (Atlas §2) from units.json + unit files.
Read-only. Re-run: python3 gen_catalogue.py <scratch_dir> > catalogue.md"""
import json,re,sys,os,collections
S=sys.argv[1]
from pathlib import Path
d=json.loads(Path(f'{S}/units.json').read_text())
fleet={u:v for u,v in d.items() if v.get('FragmentPath','').startswith('/etc/systemd/system/')}
def f(v,k): return (v.get(k) or '').strip()
SKIP={'system.slice','sysinit.target','basic.target','-.mount','network.target','network-online.target','systemd-journald.socket','local-fs.target','time-sync.target','multi-user.target','nss-lookup.target','remote-fs.target','tmp.mount','timers.target','paths.target','sockets.target','shutdown.target','var.mount','home.mount','time-set.target','local-fs-pre.target','systemd-udev-settle.service','graphical.target','systemd-tmpfiles-setup.service','network-pre.target'}
def deps(v):
    out=[]
    for k in ('Requires','BindsTo','PartOf','Wants','After'):
        vals=[x for x in f(v,k).split() if x not in SKIP and not x.startswith('"system-') and not x.endswith('.timer') and not x.endswith('.path')]
        if vals: out.append(f"{k}={','.join(vals)}")
    return ' '.join(out) or '—'
# reverse deps
rev=collections.defaultdict(set)
for u,v in d.items():
    for k in ('Requires','BindsTo','PartOf','Wants','After'):
        for x in f(v,k).split():
            if x in fleet and x!=u and not u.endswith('.timer') and not u.endswith('.path'): rev[x].add(f"{u}({k})")
def sched(u):
    try: t=Path(f'{S}/unitfiles/{u}').read_text()
    except: return ''
    keys=('OnCalendar','OnUnitActiveSec','OnBootSec')
    return '; '.join(f"{k}={m.strip()}" for k in keys for m in re.findall(rf'^\s*{k}\s*=\s*(.+)$',t,re.M))
def exe(v):
    m=re.search(r'argv\[\]=([^;]+)',f(v,'ExecStart')); return (m.group(1).strip() if m else '')
FAM=[('Foundation / platform','^(assemble-|storage-boot|iptables|hugepages|thp-|nic-park|lumen-power[.]|cloudflared|desktop-commander|mcp-gateway|memory-mcp|ollama|thoth-origin|lumen-maint)'),
 ('Vault HA + backups','^(wadjet-ha|vw-|lumen-backup)'),
 ('Alert path + triage (aq/ntfy)','^(lumen-alert-queue|lumen-aq-|alert-deadman|deadman-|lumen-gatus-ntfy|ntfy-onfailure|lumen-command-receiver|lumen-alert-hygiene)'),
 ('Guards / watchdogs / sentinels','^(lumen-failed-units|lumen-cron-deadman|lumen-self-healer|lumen-guardian|lumen-node-killer|mcp-pressure|lumen-model-watchdog|sentinel-probes|lumen-sentinel-watch|fleet-auth-ssh|gatus-sshauth|dispatch-watchdog|lumen-foreman-health|lumen-foreman-selfheal|lumen-foreman-gate|lumen-foreman-nightly|lumen-foreman-window|lumen-load-watch|lumen-ecc|lumen-embed-guard|lumen-public-guardian|lumen-lpp-stuck|lumen-rosco|lumen-split-watch|lumen-rerank-thread|litellm-auth|lumen-litellm-auth|langfuse-idle|power-remediate|lumen-retirement|lumen-sqlite-health|lumen-state-reconcile|lumen-liveness-clear|lumen-tickle|lumen-monitor|n8n-watcher|lumen-bc250-watch|lumen-kiosk-idle)'),
 ('Drift / integrity guards (git, units, config, secrets)','^(unit-drift|wq-drift|primary-tree|drift-assert|dc-config|dc-patch|git-guard|repo-visibility|lumen-repo-sweep|commit-hygiene|lumen-pat-scan|lumen-dispatch-lint|lumen-read-dir|lumen-ui-drift|lumen-trivy|gitea-version|lumen-disk-hygiene|lumen-scratch-sweep|lumen-memory-audit|lumen-model-audit)'),
 ('Orchestration (wq/foreman/agent lanes)','^(lumen-foreman-dispatch|lumen-fleet-|lumen-intel|lumen-scout|lumen-signal|lumen-agent|session-handoff|lumen-prompt-forge|lumen-mission-control|lumen-scribe|lumen-copilot|lumen-cord|lumen-proposals-ui|lumen-dgm|lumen-skillhex|sia-news|renovate|lumen-intervention)'),
 ('Minerva memory + pipeline','^(minerva-|lumen-memoryd|lumen-dream|lumen-reflector|lumen-convo-learn|lumen-worklog|lumen-telemetry|lumen-research-index|lumen-recall-bench|lumen-rerank[.]|lumen-familiarity|lumen-judgearena|lumen-pelican|lumen-embed|langfuse-evaluator)'),
 ('Messenger / persona / public surfaces','^(lumen-messenger|lumen-mail-sync|lumen-pokemon|lumen-lpp-frontier|legal-resource|lumen-bench|projects-app|fleet-status|fleet-inventory|lumen-dashboard|beszel|n8n[.]|lumen-briefing|lumen-news-digest|lumen-weekly-report|system-map|lumen-github-watch|lumen-pr-watch|jay-watch|lumen-community-watch|lumen-ebay-watch|lumen-bench-checker)'),
 ('Power / plugs / telemetry','^(lumen-plug|lumen-powerdash|wan-usage|wq394|vlogs|deepseek-cost|lumen-deepseek-spend|lumen-budget-alert)'),
]
assigned={}
for u in fleet:
    for name,rx in FAM:
        if re.search(rx,u): assigned[u]=name; break
    else: assigned[u]='Other'
order=[n for n,_ in FAM]+['Other']
print("## 2. Unit catalogue (generated 2026-09-10 from `systemctl show`; 300 fleet-authored units under /etc/systemd/system)\n")
print("Legend: **state** = ActiveState/SubState at capture. `deps` = declared Requires/BindsTo/PartOf/Wants/After (distro targets elided). `dependents` = fleet units that declare a dependency on this one. `alert` = OnFailure hook if any. Timers: cadence from the unit file.\n")
for fam in order:
    us=sorted(u for u in fleet if assigned[u]==fam)
    if not us: continue
    svcs=[u for u in us if u.endswith('.service')]; tims=[u for u in us if u.endswith('.timer')]; paths=[u for u in us if u.endswith('.path')]
    print(f"### 2.{order.index(fam)+1} {fam} ({len(svcs)} services, {len(tims)} timers, {len(paths)} paths)\n")
    print("| unit | state | what / ExecStart | type/restart | cadence (trigger) | deps | dependents | alert |")
    print("|---|---|---|---|---|---|---|---|")
    for u in svcs+paths:
        v=fleet[u]; st=f"{f(v,'ActiveState')}/{f(v,'SubState')}"
        if st.startswith('failed'): st=f"**{st}**"
        desc=f(v,'Description').replace('|','\\|')
        ex=exe(v).replace('|','\\|')
        what=f"{desc}<br>`{ex[:120]}`" if ex else desc
        trig=f(v,'TriggeredBy').split()
        cad=[]
        for t in trig:
            if t in fleet: cad.append(f"{sched(t) or 'path'} ({t})")
        cadence='; '.join(cad) if cad else ('daemon' if f(v,'SubState')=='running' else ('boot-oneshot' if f(v,'Type')=='oneshot' and not trig else '—'))
        tr=f"{f(v,'Type') or '?'}/{f(v,'Restart') or 'no'}"
        if f(v,'NRestarts') not in ('','0'): tr+=f" (NRestarts={f(v,'NRestarts')})"
        if u.endswith('.path'): tr='path'; cadence='PathChanged: '+(re.sub(r'.*?PathChanged=','',Path(f'{S}/unitfiles/{u}').read_text(),flags=re.S).split('\n')[0] if os.path.exists(f'{S}/unitfiles/{u}') else '?')
        dp=deps(v).replace('|','\\|'); rv=', '.join(sorted(rev.get(u,[]))) or '—'
        al=f(v,'OnFailure') or '—'
        print(f"| `{u}` | {st} | {what} | {tr} | {cadence} | {dp} | {rv} | {al} |")
    print()
