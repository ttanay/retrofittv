#!/usr/bin/env python3
"""Keep web pages away from Soloist's WebSocket API.

Soloist doesn't check the Origin header, so any page open in a browser on the
Pi could connect to it and control playback. This proxy listens on
127.0.0.1:SOLOIST_WS_PORT and refuses every handshake that carries an Origin
header (browsers always send one; `soloist ctl` and scripts don't). Allowed
connections are passed through byte for byte to Soloist on
127.0.0.1:SOLOIST_WS_INTERNAL_PORT, which a firewall rule lets only this
proxy's user reach.

Started by soloist-ws-proxy.service. Uses only the Python standard library.
"""

import asyncio
import os
import sys

LISTEN_PORT = int(os.environ["SOLOIST_WS_PORT"])
UPSTREAM_PORT = int(os.environ.get("SOLOIST_WS_INTERNAL_PORT") or 19090)
MAX_HEADER_BYTES = 16 * 1024
HEADER_TIMEOUT = 5


async def refuse(writer, status, reason):
    print(f"Refused connection: {reason}", file=sys.stderr, flush=True)
    writer.write(f"HTTP/1.1 {status}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode())
    try:
        await writer.drain()
    except ConnectionError:
        pass
    writer.close()


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except ConnectionError:
        pass
    finally:
        writer.close()


async def handle(reader, writer):
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEADER_TIMEOUT)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        writer.close()
        return

    headers = {}
    for line in head.split(b"\r\n")[1:]:
        name, sep, value = line.partition(b":")
        if sep:
            headers[name.strip().lower()] = value.strip()

    for name in (b"origin", b"sec-websocket-origin"):
        if name in headers:
            return await refuse(writer, "403 Forbidden", f"browser request from Origin {headers[name]!r}")
    if headers.get(b"upgrade", b"").lower() != b"websocket":
        return await refuse(writer, "403 Forbidden", "not a WebSocket handshake")

    try:
        up_reader, up_writer = await asyncio.open_connection("127.0.0.1", UPSTREAM_PORT)
    except OSError as e:
        return await refuse(writer, "502 Bad Gateway", f"Soloist isn't reachable ({e})")

    up_writer.write(head)
    await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))


async def main():
    server = await asyncio.start_server(handle, "127.0.0.1", LISTEN_PORT, limit=MAX_HEADER_BYTES)
    print(f"Forwarding 127.0.0.1:{LISTEN_PORT} to Soloist on port {UPSTREAM_PORT}", file=sys.stderr, flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
