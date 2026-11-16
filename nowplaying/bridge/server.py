"""Read-only HTTP server for the TV page: static files, art and an SSE stream.

GET only, 127.0.0.1 only, and requests must carry our own Host header, so a
web page elsewhere can't reach it through DNS rebinding.
"""

import asyncio
import json
import logging
import os
import re

log = logging.getLogger("http")

TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript",
         ".ttf": "font/ttf", ".jpg": "image/jpeg", ".txt": "text/plain"}
IMG_NAME = re.compile(r"^[0-9a-f]{16}-(art|thumb|bg)\.jpg$")
KEEPALIVE_S = 15


class Hub:
    """Holds the latest snapshot and fans updates out to SSE clients."""

    def __init__(self):
        self.snapshot = None
        self.clients = set()

    def publish(self, snapshot):
        data = json.dumps(snapshot, separators=(",", ":"))
        if data == self.snapshot:
            return False
        self.snapshot = data
        self._send("state", data)
        return True

    def event(self, name, payload):
        self._send(name, json.dumps(payload))

    def _send(self, name, data):
        for q in self.clients:
            q.put_nowait((name, data))


class Server:
    def __init__(self, hub, port, www_dir, img_dir, health):
        self.hub = hub
        self.port = port
        self.img_dir = img_dir
        self.health = health
        self.hosts = {f"127.0.0.1:{port}".encode(), f"localhost:{port}".encode()}
        # Serve only the files that exist at startup; no paths from the request.
        self.static = {}
        for root, _, files in os.walk(www_dir):
            for f in files:
                path = os.path.join(root, f)
                self.static["/" + os.path.relpath(path, www_dir)] = path
        self.static["/"] = self.static["/index.html"]

    async def start(self):
        return await asyncio.start_server(self.handle, "127.0.0.1", self.port, limit=16 * 1024)

    async def handle(self, reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            lines = head.split(b"\r\n")
            method, target, _ = lines[0].split(b" ", 2)
            headers = {}
            for line in lines[1:]:
                k, sep, v = line.partition(b":")
                if sep:
                    headers[k.strip().lower()] = v.strip()
            path = target.split(b"?")[0].decode("ascii")
            if headers.get(b"host") not in self.hosts:
                await self.respond(writer, "403 Forbidden")
            elif method != b"GET":
                await self.respond(writer, "405 Method Not Allowed")
            elif path == "/events":
                await self.events(writer)
            elif path == "/healthz":
                await self.respond(writer, "200 OK", json.dumps(self.health()).encode(), "application/json")
            elif path.startswith("/img/") and IMG_NAME.match(path[5:]):
                await self.file(writer, os.path.join(self.img_dir, path[5:]), "public, max-age=86400")
            elif path in self.static:
                await self.file(writer, self.static[path], "no-cache")
            else:
                await self.respond(writer, "404 Not Found")
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError,
                ValueError, UnicodeDecodeError, ConnectionError):
            pass
        finally:
            writer.close()

    async def respond(self, writer, status, body=b"", ctype="text/plain", cache="no-store"):
        writer.write(f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
                     f"Cache-Control: {cache}\r\nConnection: close\r\n\r\n".encode() + body)
        await writer.drain()

    async def file(self, writer, path, cache):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return await self.respond(writer, "404 Not Found")
        ctype = TYPES.get(os.path.splitext(path)[1], "application/octet-stream")
        await self.respond(writer, "200 OK", body, ctype, cache)

    async def events(self, writer):
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                     b"Cache-Control: no-store\r\nConnection: close\r\n\r\n")
        q = asyncio.Queue()
        if self.hub.snapshot:
            q.put_nowait(("state", self.hub.snapshot))
        self.hub.clients.add(q)
        try:
            while True:
                try:
                    name, data = await asyncio.wait_for(q.get(), KEEPALIVE_S)
                    writer.write(f"event: {name}\ndata: {data}\n\n".encode())
                except asyncio.TimeoutError:
                    writer.write(b": keepalive\n\n")
                await writer.drain()
        finally:
            self.hub.clients.discard(q)
