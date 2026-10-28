#!/usr/bin/env python3
"""ops-apply — the allow-listed client a worker uses to talk to the gate
(design §3.2 topology: "ops-apply submit <manifest.json> (allow-listed
client, no root)"). Agent-facing surface is deliberately tiny: it reads a
manifest file, sends one newline-delimited JSON line to the gate socket,
and prints the JSON reply. All validation happens server-side (V1-V10);
this client trusts nothing and enforces nothing.
"""
import argparse
import json
import socket
import sys

DEFAULT_SOCK = "/run/ops-apply/gate.sock"


def _send(sock_path, request):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.connect(sock_path)
        conn.sendall(json.dumps(request).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.decode())


def main(argv=None):
    p = argparse.ArgumentParser(prog="ops-apply")
    p.add_argument("--sock", default=DEFAULT_SOCK)
    sub = p.add_subparsers(dest="verb", required=True)

    submit = sub.add_parser("submit")
    submit.add_argument("manifest_path")

    approve = sub.add_parser("approve")
    approve.add_argument("ticket", type=int)
    approve.add_argument("req")

    status = sub.add_parser("status")
    status.add_argument("ticket", type=int)
    status.add_argument("req")

    revert = sub.add_parser("revert")
    revert.add_argument("ticket", type=int)
    revert.add_argument("req")

    args = p.parse_args(argv)

    if args.verb == "submit":
        with open(args.manifest_path) as f:
            manifest = json.load(f)
        request = {"verb": "submit", "manifest": manifest}
    elif args.verb == "approve":
        request = {"verb": "approve", "ticket": args.ticket, "req": args.req}
    elif args.verb == "status":
        request = {"verb": "status", "ticket": args.ticket, "req": args.req}
    elif args.verb == "revert":
        request = {"verb": "revert", "ticket": args.ticket, "req": args.req}
    else:  # pragma: no cover - argparse enforces the choice set above
        raise AssertionError(args.verb)

    reply = _send(args.sock, request)
    print(json.dumps(reply, sort_keys=True))
    return 0 if reply.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
