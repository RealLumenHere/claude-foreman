#!/bin/bash
# wq#5122 Phase 6+7 on Maksoor: fix boot config + restore Neith identity into loop-mounted image.
# Usage: sudo phase67.sh <mountpoint> <new-uuid> <identity.tar> <bootcfg.tar>
set -euo pipefail
M=$1; NEW=$2; IDT=$3; BCT=$4; OLD=fff4af74-d511-4dba-869e-4dfc62f3f4ec
findmnt "$M" >/dev/null; [[ $(findmnt -no UUID "$M") == "$NEW" ]] || { echo "UUID mismatch"; exit 1; }
W=$(mktemp -d /root/neith-restore.XXXXXX); mkdir -p $W/id $W/bc
tar xpf "$IDT" -C $W/id --numeric-owner; tar xpf "$BCT" -C $W/bc --numeric-owner
# --- Phase 5 tail: excluded mountpoints
mkdir -p $M/{dev,proc,sys,run,tmp,mnt,media,data}; chmod 1777 $M/tmp
# --- Phase 6: boot
install -o0 -g0 -m664 $W/bc/boot/extlinux/extlinux.conf $M/boot/extlinux/extlinux.conf
sed -i "s/$OLD/$NEW/g" $M/boot/extlinux/extlinux.conf
sed -i "s/^rootdev=UUID=$OLD\$/rootdev=UUID=$NEW/" $M/boot/armbianEnv.txt
sed -i -e "s/$OLD/$NEW/g" -e "/LABEL=p1pro-data/d" $M/etc/fstab
DTB=$(awk '/^ *fdt /{print $2}' $M/boot/extlinux/extlinux.conf)
for f in $DTB $(awk '/^ *(kernel|initrd) /{print $2}' $M/boot/extlinux/extlinux.conf); do
  ( cd $M && test -s ".$f" ) || { echo "MISSING boot file $f"; exit 1; }; done
grep -q pc-plus <<<"$DTB" || { echo "DTB not pc-plus"; exit 1; }
# --- Phase 7: identity
install -o0 -g0 -m644 $W/id/etc/hostname $M/etc/hostname
install -o0 -g0 -m644 $W/id/etc/hosts $M/etc/hosts
install -o0 -g0 -m444 $W/id/etc/machine-id $M/etc/machine-id
rm -f $M/etc/ssh/ssh_host_*; cp -a $W/id/etc/ssh/ssh_host_* $M/etc/ssh/
rm -rf $M/var/lib/tailscale; cp -a $W/id/var/lib/tailscale $M/var/lib/tailscale
for f in neith 90-asfar mcp-gateway-watchdog; do
  visudo -c -q -f $W/id/etc/sudoers.d/$f; install -o0 -g0 -m440 $W/id/etc/sudoers.d/$f $M/etc/sudoers.d/$f; done
python3 - "$M" "$W/id" <<'PY'
import sys
M,I=sys.argv[1],sys.argv[2]; USERS=("asfar","neith")
rd=lambda p:[l.rstrip("\n") for l in open(p)]
def wr(p,L): open(p,"w").write("\n".join(L)+"\n")
pw,sh,gr,gs=[rd(f"{M}/etc/{f}") for f in ("passwd","shadow","group","gshadow")]
npw,nsh,ngr=[rd(f"{I}/etc/{f}") for f in ("passwd","shadow","group")]
uids={l.split(":")[2] for l in pw}; gids={l.split(":")[2] for l in gr}
for u in USERS:
    p=[l for l in npw if l.split(":")[0]==u][0]; s=[l for l in nsh if l.split(":")[0]==u][0]
    g=[l for l in ngr if l.split(":")[0]==u][0]
    assert not any(l.split(":")[0]==u for l in pw), f"{u} exists"
    assert p.split(":")[2] not in uids and g.split(":")[2] not in gids, f"{u} id clash"
    pw.append(p); sh.append(s); gr.append(g); gs.append(f"{u}:!::")
memb={}
for l in ngr:
    n,_,_,m=l.split(":")
    for u in m.split(",") if m else []:
        if u in USERS: memb.setdefault(n,[]).append(u)
def addm(L,field):
    out=[]
    for l in L:
        f=l.split(":"); n=f[0]
        if n in memb:
            cur=[x for x in f[field].split(",") if x]
            for u in memb[n]:
                if u not in cur: cur.append(u)
            f[field]=",".join(cur)
        out.append(":".join(f))
    return out
gr=addm(gr,3); gs=addm(gs,3)
for p,L in (("passwd",pw),("shadow",sh),("group",gr),("gshadow",gs)): wr(f"{M}/etc/{p}",L)
print("users merged; memberships:",memb)
PY
for u in asfar neith; do
  read -r uid gid home <<<"$(awk -F: -v u=$u '$1==u{print $3,$4,$6}' $M/etc/passwd)"
  mkdir -p $M$home; cp -an $M/etc/skel/. $M$home/
  [[ -d $W/id$home/.ssh ]] && cp -a $W/id$home/.ssh $M$home/
  chown -R $uid:$gid $M$home; chmod 750 $M$home
done
mkdir -p -m700 $M/root/.ssh
cat $M/root/.ssh/authorized_keys $W/id/root/.ssh/authorized_keys 2>/dev/null | sort -u > $W/rk
install -o0 -g0 -m600 $W/rk $M/root/.ssh/authorized_keys
rm -rf "$W"
echo "== verify"; cat $M/etc/hostname; cat $M/boot/extlinux/extlinux.conf; cat $M/etc/fstab; grep rootdev $M/boot/armbianEnv.txt
grep -E "^(asfar|neith|lumen|ptah):" $M/etc/passwd; grep -E "^(sudo|docker):" $M/etc/group
ls -la $M/etc/ssh/ssh_host_*.pub $M/var/lib/tailscale/tailscaled.state | awk '{print $1,$3,$4,$NF}'
echo PHASE67_OK
