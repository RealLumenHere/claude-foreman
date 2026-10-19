#!/usr/bin/env python3
"""Headless account + device pairing client for Remote Desktop Commander (wq#4330).

Subcommands:
  signup          create account (email from BOT_ADDRESS, password generated -> account.json 0600)
  confirm <url>   follow the email confirmation link, capture tokens from the redirect fragment
  session         exchange captured tokens for a backend session cookie (cookies.json)
  probe <url>     GET a URL with the session cookie jar, dump status + body head

State lives in /tmp/fworker-wq4330/rdc/ (account.json, tokens.json, cookies.json), all 0600.
No secret is ever printed to stdout.
"""
import json
import os
import secrets
import string
import sys
import urllib.request
import urllib.error
import urllib.parse
import http.cookiejar

RUN = "/tmp/fworker-wq4330/rdc"
ACCOUNT = os.path.join(RUN, "account.json")
TOKENS = os.path.join(RUN, "tokens.json")
COOKIES = os.path.join(RUN, "cookies.json")
AUTH_BASE = "https://auth.desktopcommander.app"
EMAIL = "lumen@lumennode.uk"
UA = "Mozilla/5.0 (X11; Linux x86_64) wq4330-pairing/1.0"


def save_private(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)
    os.chmod(path, 0o600)


def load(path):
    with open(path) as f:
        return json.load(f)


def http_json(url, payload, cookie_header=None):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", UA)
    req.add_header("Origin", AUTH_BASE)
    if cookie_header:
        req.add_header("Cookie", cookie_header)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}"), r.headers
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            body = json.loads(body)
        except Exception:
            pass
        return e.code, body, e.headers


def redact(obj):
    """Deep-copy with token-looking values masked for safe printing."""
    if isinstance(obj, dict):
        return {k: ("<redacted>" if any(w in k.lower() for w in ("token", "password", "secret", "key", "jwt", "session")) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    if isinstance(obj, str) and obj.startswith("eyJ"):
        return "<redacted-jwt>"
    return obj


def cmd_signup():
    alphabet = string.ascii_letters + string.digits
    password = "".join(secrets.choice(alphabet) for _ in range(24))
    save_private(ACCOUNT, {"email": EMAIL, "password": password, "service": "desktopcommander.app", "ticket": "wq4330"})
    status, body, _ = http_json(f"{AUTH_BASE}/auth/backend/signup",
                                {"email": EMAIL, "password": password, "auth_type": "device"})
    print("signup status:", status)
    print(json.dumps(redact(body), indent=2)[:800])


def cmd_confirm(url):
    # The confirmation link redirects (possibly via several hops) ending with
    # tokens in a URL fragment. Don't follow the final redirect automatically —
    # capture Location headers manually.
    cur = url
    for hop in range(6):
        req = urllib.request.Request(cur, method="GET")
        req.add_header("User-Agent", UA)
        opener = urllib.request.build_opener(NoRedirect())
        try:
            r = opener.open(req, timeout=30)
            status, loc = r.status, r.headers.get("Location")
        except urllib.error.HTTPError as e:
            status, loc = e.code, e.headers.get("Location")
        print(f"hop {hop}: {status} -> {redact_url(loc)}")
        if not loc:
            break
        frag = urllib.parse.urlparse(loc).fragment
        qs = urllib.parse.parse_qs(frag) if frag else {}
        if "access_token" in qs:
            save_private(TOKENS, {k: v[0] for k, v in qs.items()})
            print("tokens captured -> tokens.json (keys:", sorted(qs.keys()), ")")
            return
        cur = urllib.parse.urljoin(cur, loc)
    print("no access_token found in redirect chain")
    sys.exit(1)


def redact_url(u):
    if not u:
        return u
    return u.split("#")[0] + ("#<fragment-redacted>" if "#" in u else "")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def cmd_session():
    t = load(TOKENS)
    status, body, headers = http_json(f"{AUTH_BASE}/auth/backend/session/create",
                                      {"access_token": t["access_token"],
                                       "refresh_token": t.get("refresh_token", t["access_token"]),
                                       "is_signup": True})
    print("session status:", status)
    print(json.dumps(redact(body), indent=2)[:600])
    cookies = headers.get_all("Set-Cookie") or []
    jar = {}
    for c in cookies:
        first = c.split(";")[0]
        name, _, value = first.partition("=")
        jar[name.strip()] = value
        attrs = ";".join(c.split(";")[1:])
        print("cookie set:", name.strip(), "| attrs:", attrs)
    if jar:
        save_private(COOKIES, jar)
        print("cookies saved -> cookies.json")


def cookie_header():
    jar = load(COOKIES)
    return "; ".join(f"{k}={v}" for k, v in jar.items())


def cmd_probe(url):
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", UA)
    req.add_header("Cookie", cookie_header())
    opener = urllib.request.build_opener(NoRedirect())
    try:
        r = opener.open(req, timeout=30)
        status, loc, body = r.status, r.headers.get("Location"), r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        status, loc, body = e.code, e.headers.get("Location"), e.read().decode(errors="replace")
    print("status:", status, "location:", redact_url(loc))
    print(body[:1200])


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "signup":
        cmd_signup()
    elif cmd == "confirm":
        cmd_confirm(sys.argv[2])
    elif cmd == "session":
        cmd_session()
    elif cmd == "probe":
        cmd_probe(sys.argv[2])
    else:
        print("unknown cmd")
        sys.exit(1)
