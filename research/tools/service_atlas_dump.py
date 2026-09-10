#!/usr/bin/env python3
"""wq#1627 read-only: dump systemd unit properties to JSON."""
import json, subprocess, sys, os
S = sys.argv[1] if len(sys.argv)>1 else "/tmp/work-1627-7cb790"
PROPS = ["Id","Description","LoadState","ActiveState","SubState","Result","FragmentPath","DropInPaths",
 "UnitFileState","ExecStart","ExecStartPre","ExecStopPost","Type","Restart","RestartUSec",
 "Requires","Requisite","Wants","BindsTo","PartOf","After","Before","Conflicts",
 "RequiredBy","WantedBy","BoundBy","ConsistsOf","TriggeredBy","Triggers","PropagatesReloadTo",
 "OnFailure","OnSuccess","WatchdogUSec","NRestarts","ExecMainStartTimestamp","StateChangeTimestamp",
 "TimersCalendar","TimersMonotonic","NextElapseUSecRealtime","LastTriggerUSec","Unit","Persistent",
 "MemoryMax","MemoryHigh","MemoryCurrent","CPUQuotaPerSecUSec","ConditionResult","ConditionPathExists",
 "PathExists","PathChanged","PathModified","DirectoryNotEmpty","NFailed","ActiveEnterTimestamp","InactiveEnterTimestamp"]
units = []
out = subprocess.run(["systemctl","list-units","--type=service,timer,path,socket","--all","--no-pager","--no-legend","--plain"],
    capture_output=True,text=True).stdout
for line in out.splitlines():
    p=line.split()
    if p: units.append(p[0])
units = sorted(set(units))
data={}
for i in range(0,len(units),40):
    chunk=units[i:i+40]
    r=subprocess.run(["systemctl","show","-p",",".join(PROPS),*chunk],capture_output=True,text=True).stdout
    cur={}
    for line in r.splitlines()+[""]:
        if not line.strip():
            if cur.get("Id"): data[cur["Id"]]=cur
            cur={}; continue
        k,_,v=line.partition("=")
        cur[k]=v
with open(f"{S}/units.json","w") as fh: json.dump(data,fh,indent=1)
fleet=[u for u,d in data.items() if d.get("FragmentPath","").startswith("/etc/systemd/system/")]
print("total",len(data),"fleet(/etc)",len(fleet))
# copy fleet unit file text
os.makedirs(f"{S}/unitfiles",exist_ok=True)
for u in fleet:
    fp=data[u]["FragmentPath"]
    try:
        with open(fp) as fh: txt=fh.read()
        for dp in (data[u].get("DropInPaths") or "").split():
            with open(dp) as fh: txt+=f"\n### DROPIN {dp}\n"+fh.read()
        with open(f"{S}/unitfiles/{u}","w") as fh: fh.write(txt)
    except Exception as e: print("ERR",u,e)
