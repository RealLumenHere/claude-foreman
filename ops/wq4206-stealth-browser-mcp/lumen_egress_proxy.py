"""Egress filter for the stealth browser's Chrome (wq#4290).

lumen_stealth_guard checks the URLs agents pass to tools, but JavaScript in the page
(execute_script, or the hostile page itself) can fetch(), XHR or open a WebSocket to any
host without naming it in a tool argument (wq#4218). The guard therefore launches every
Chrome with this proxy as --proxy-server and loopback removed from Chrome's implicit
bypass list, so each connection the browser opens, redirects included, comes through
here.

For each CONNECT (https, ws, wss) or absolute-URI http request, the proxy resolves the
host itself, refuses with 403 unless every address is public (the guard's rule), and
connects to an address it checked. Every upstream socket it opens is to a checked public
address, whatever bytes Chrome sends over it afterwards.

Stdlib only; runs on its own event loop in a daemon thread of the launcher process.
"""
import asyncio
import threading
from urllib.parse import urlsplit

import lumen_stealth_guard as guard

DROP_HEADERS = (b"proxy-connection:", b"proxy-authorization:", b"connection:", b"keep-alive:")


def target_of(method, target):
    """(host, port, origin-form path or None) for a proxy request line, or PolicyError."""
    if method.upper() == "CONNECT":
        parts = urlsplit("//" + target)
        if not parts.hostname or parts.port is None:
            raise guard.PolicyError(f"bad CONNECT target {target!r}")
        return parts.hostname, parts.port, None
    parts = urlsplit(target)
    if parts.scheme.lower() != "http" or not parts.hostname:
        raise guard.PolicyError(f"not an absolute http URL: {target[:200]!r}")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return parts.hostname, parts.port or 80, path


def rewrite_head(method, path, version, header_block):
    """The request head to send upstream: origin-form, proxy headers dropped, one request
    per connection so a reused client connection cannot follow on to another host."""
    lines = [line for line in header_block.split(b"\r\n")
             if line and not line.lower().startswith(DROP_HEADERS)]
    first = f"{method} {path} {version}".encode("latin-1")
    return b"\r\n".join([first, *lines, b"Connection: close", b"", b""])


async def _connect(addresses, port):
    error = None
    for address in addresses:
        try:
            return await asyncio.wait_for(asyncio.open_connection(address, port), 20)
        except (OSError, asyncio.TimeoutError) as exc:
            error = exc
    raise OSError(f"connect failed: {error}")


async def _pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except OSError:
        pass


def _reply(writer, status, reason):
    body = f"lumen egress proxy: {reason}\n".encode()
    writer.write(f"HTTP/1.1 {status} {'Forbidden' if status == 403 else 'Bad Gateway'}\r\n"
                 f"Content-Type: text/plain\r\nContent-Length: {len(body)}\r\n"
                 "Connection: close\r\n\r\n".encode() + body)


async def handle(reader, writer):
    upstream = None
    try:
        head = await reader.readuntil(b"\r\n\r\n")
        request_line, _, header_block = head[:-4].partition(b"\r\n")
        method, target, version = request_line.decode("latin-1").split(" ", 2)
        host, port, path = target_of(method, target)
        addresses = await asyncio.get_running_loop().run_in_executor(None, guard.public_addresses, host)
        up_reader, upstream = await _connect(addresses, port)
        if path is None:
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        else:
            upstream.write(rewrite_head(method, path, version, header_block))
        await writer.drain()
        await upstream.drain()
        pipes = [asyncio.ensure_future(_pipe(reader, upstream)), asyncio.ensure_future(_pipe(up_reader, writer))]
        _, pending = await asyncio.wait(pipes, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
    except guard.PolicyError as exc:
        _reply(writer, 403, exc)
    except (OSError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
        _reply(writer, 502, exc)
    finally:
        for stream in (upstream, writer):
            if stream is not None:
                stream.close()


def start(host="127.0.0.1"):
    """Serve on an ephemeral loopback port in a daemon thread; returns the proxy URL."""
    loop = asyncio.new_event_loop()
    server = loop.run_until_complete(asyncio.start_server(handle, host, 0))
    threading.Thread(target=loop.run_forever, name="lumen-egress-proxy", daemon=True).start()
    return "http://%s:%d" % server.sockets[0].getsockname()[:2]
