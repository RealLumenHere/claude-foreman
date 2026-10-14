#!/usr/bin/env python3
"""mcpw-diag.py -- end-to-end health check for the mcpw OAuth gateway:
register -> authorize -> token -> MCP `initialize`.

Rewritten as a durable copy of the /tmp/mcpw_diag.py pattern referenced
throughout claude_memory.md's 2026-09-25 mcpw outage notes and the
CONTROL-PLANE HARD RULES ("verified end-to-end test... BEFORE declaring
success. Health = full MCP initialize, NOT 'mcpw / returns 200'.") -- that
script lived in /tmp and is gone. This is the version that should persist.

Why this can't be fully unattended: step 3 (/authorize) is gated by
Cloudflare Access (an interactive browser login as the allowed user, or
today's temporary AUTHORIZE_PASSPHRASE form per the 2026-09-25 outage
notes) -- by design, per control-plane rule 3, nobody but Frank drives a
real login through the front door. This script does DCR + PKCE + the local
plumbing, prints the /authorize URL for Frank to open, and waits for the
authorization code.

For the WATCHDOG's automated per-minute probe (mcp-gateway-watchdog.sh
--mode mcpw), do not re-run this dance every minute -- run it ONCE by hand
after each P1 box is behind mcpw, save the resulting access_token to
/home/<node>/.secrets/mcpw-watchdog.token (0600), and re-run this script to
mint a fresh one before the 7-day access-token TTL (or 30-day refresh-token
TTL) expires. That renewal is a known manual gap -- see README.md's
"Known limitations" section.

Usage:
  python3 mcpw-diag.py --base-url https://mcpw.lumennode.uk
  python3 mcpw-diag.py --base-url https://mcpw.lumennode.uk --save-token /home/p1-primary/.secrets/mcpw-watchdog.token
"""
import argparse
import base64
import hashlib
import json
import os
import secrets
import stat
import sys
import urllib.parse
import urllib.request
import urllib.error


def post_json(url, payload, headers=None):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                  headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body}


def post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True, help="e.g. https://mcpw.lumennode.uk")
    ap.add_argument("--save-token", help="path to save the access_token to (0600)")
    ap.add_argument("--redirect-uri", default="http://localhost:8765/callback",
                     help="does not need to be a real listener -- we read the code from the redirected URL Frank pastes back")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    print(f"=== 1/4 register (DCR) against {base}/register ===")
    status, reg = post_json(f"{base}/register", {
        "client_name": "mcpw-diag",
        "redirect_uris": [args.redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    })
    if status not in (200, 201):
        print(f"FAIL: /register -> {status} {reg}", file=sys.stderr)
        sys.exit(1)
    client_id = reg["client_id"]
    print(f"  client_id={client_id}")

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)

    print(f"\n=== 2/4 authorize -- OPEN THIS URL IN A BROWSER LOGGED IN AS THE ALLOWED USER ===")
    authorize_url = f"{base}/authorize?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": args.redirect_uri,
        "scope": "mcp",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    print(f"  {authorize_url}")
    print(f"\n  After CF Access login, you'll land on {args.redirect_uri}?code=...&state=...")
    print(f"  (the browser will show a connection error -- that's expected, nothing listens there;")
    print(f"  copy the FULL resulting URL from the address bar.)")
    pasted = input("\nPaste the full redirected URL (or just the ?code=... part): ").strip()
    if "code=" not in pasted:
        print("FAIL: no code= found in what you pasted", file=sys.stderr)
        sys.exit(1)
    qs = urllib.parse.urlparse(pasted if pasted.startswith("http") else f"x://x{pasted}").query
    params = urllib.parse.parse_qs(qs)
    code = params.get("code", [None])[0]
    returned_state = params.get("state", [None])[0]
    if not code:
        print("FAIL: could not parse code from pasted value", file=sys.stderr)
        sys.exit(1)
    if returned_state and returned_state != state:
        print(f"WARNING: state mismatch (sent {state}, got {returned_state}) -- continuing anyway for a manual diag run")

    print(f"\n=== 3/4 token exchange against {base}/token ===")
    status, tok = post_form(f"{base}/token", {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": args.redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    })
    if status != 200 or "access_token" not in tok:
        print(f"FAIL: /token -> {status} {tok}", file=sys.stderr)
        sys.exit(1)
    access_token = tok["access_token"]
    print(f"  access_token acquired (expires_in={tok.get('expires_in', '?')}s)")

    print(f"\n=== 4/4 MCP initialize against {base}/mcp ===")
    init_payload = {
        "jsonrpc": "2.0", "id": "mcpw-diag-init", "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "mcpw-diag", "version": "1"}},
    }
    status, result = post_json(f"{base}/mcp", init_payload, headers={"Authorization": f"Bearer {access_token}"})
    if status != 200 or "result" not in result:
        print(f"FAIL: /mcp initialize -> {status} {result}", file=sys.stderr)
        sys.exit(1)
    server_info = result["result"].get("serverInfo", {})
    print(f"  PASS: initialize OK, serverInfo={server_info}")

    if args.save_token:
        fd = os.open(args.save_token, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(access_token)
        os.chmod(args.save_token, stat.S_IRUSR | stat.S_IWUSR)
        print(f"\nSaved access_token to {args.save_token} (0600).")
        print(f"Refresh token (30d TTL) -- save this somewhere safe if you want to script renewal later:")
        print(f"  {tok.get('refresh_token', '(none returned)')}")

    print(f"\n=== ALL CHECKS PASSED: register -> authorize -> token -> initialize, end to end ===")


if __name__ == "__main__":
    main()
