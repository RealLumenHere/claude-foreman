"""argv: <module.py> <workdir> <keystroke file or -> <cli args...>
Runs drive.py under script(1) (a real pty, the wq#5216 bypass). Waits for each
passphrase prompt before typing (getpass flushes typeahead with TCSAFLUSH), sends
one keystroke line per prompt. Prints status lines only, never a token body."""
import os, select, subprocess, sys, time

mod, work, keys = sys.argv[1], sys.argv[2], sys.argv[3]
cli = " ".join(sys.argv[4:])
inner = f"{sys.executable} /tmp/fworker-wq5235/probe/drive.py {mod} {work} {cli}"
lines = [] if keys == "-" else open(keys, "rb").read().splitlines(keepends=True)
p = subprocess.Popen(["script", "-qec", inner, "/dev/null"], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
buf, deadline, sent = b"", time.time() + 60, 0
while time.time() < deadline:
    r, _, _ = select.select([p.stdout], [], [], 0.5)
    if r:
        chunk = os.read(p.stdout.fileno(), 4096)
        if not chunk:
            break
        buf += chunk
        if buf.count(b": ") > sent and buf.rstrip().endswith(b":"):
            time.sleep(0.3)
            p.stdin.write(lines[sent] if sent < len(lines) else b"\x04")
            p.stdin.flush()
            sent += 1
    elif p.poll() is not None:
        break
try:
    p.kill()
except Exception:
    pass
out = buf.decode(errors="replace").replace("\r", "")
for line in out.splitlines():
    if line.startswith("RC ") or "passphrase" in line or "minted" in line or "terminal" in line or "Error" in line:
        print("  |", line[:160])
print("prompts answered", sent)
