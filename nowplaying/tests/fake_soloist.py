#!/usr/bin/env python3
"""A fake Soloist WebSocket API for working on the bridge and page without a Pi.

    python3 tests/fake_soloist.py [--port 9090]        run the fake
    python3 tests/fake_soloist.py send pause           send it a command
    (play, pause, skip_next, skip_prev, seek MS, set_volume N, activate, deactivate)

It speaks the same protocol as the real API for the parts the bridge uses.
Then run the bridge against it:
    NOWPLAYING_CEC=0 CACHE_DIRECTORY=/tmp/np-cache python3 bridge
"""

import argparse
import asyncio
import copy
import json
import os
import sys
import time

import websockets

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "fixtures", "playback_state.json")) as f:
    REAL = json.load(f)


def make_tracks():
    first = REAL["item"]
    second = copy.deepcopy(first)
    second["uri"] = "spotify:track:fake-strange-brew"
    second["decorations"]["identity"]["name"] = "Strange Brew"
    second["decorations"]["playback"]["duration_ms"] = 166000
    third = {
        "uri": "spotify:track:fake-long-title", "entity_type": "track",
        "decorations": {
            "identity": {"name": "A Very Long Song Title That Keeps Going And Going Until It Needs To Wrap"},
            "creators": [{"entity": {"decorations": {"identity": {"name": "First Artist"}}}},
                         {"entity": {"decorations": {"identity": {"name": "Second Artist"}}}}],
            "parent": {"entity": {"uri": "spotify:album:fake", "decorations": {"identity": {"name": "No Cover Art"}}}},
            "playback": {"duration_ms": 61000, "content_ratings": ["explicit"]},
        },
    }
    return [first, second, third]


PLAYLIST = {"uri": "spotify:playlist:fake", "entity_type": "playlist",
            "decorations": {"identity": {"name": "Classic Rock"}}}


class Fake:
    def __init__(self):
        self.tracks = make_tracks()
        self.index = 0
        self.status = "playing"
        self.active = True
        self.volume = 70
        self.anchor = (0, time.time())  # (position_ms, wall time) when last set
        self.clients = set()

    def position_ms(self):
        pos, at = self.anchor
        return pos + ((time.time() - at) * 1000 if self.status == "playing" else 0)

    def position(self):
        return {"position_ms": int(self.position_ms()), "timestamp_ms": int(time.time() * 1000),
                "speed": 1 if self.status == "playing" else 0}

    def playback_state(self):
        return {"type": "playback_state", "status": self.status, "item": self.tracks[self.index],
                "context": PLAYLIST, "position": self.position(), "volume": self.volume,
                "is_active": self.active, "options": {"repeat": "off", "shuffle": False}}

    def queue(self):
        nxt = [{"uid": t["uri"], "source": "context", "item": t}
               for t in self.tracks[self.index + 1:] + self.tracks[:self.index]]
        return {"type": "queue_changed", "previous": [], "upcoming": nxt}

    async def broadcast(self, *msgs):
        for ws in list(self.clients):
            for m in msgs:
                try:
                    await ws.send(json.dumps(m))
                except websockets.exceptions.ConnectionClosed:
                    pass

    async def skip(self, step):
        self.index = (self.index + step) % len(self.tracks)
        self.anchor = (0, time.time())
        await self.broadcast({"type": "track_changed", "item": self.tracks[self.index]},
                             {"type": "position_sync", "position": self.position()}, self.queue())

    async def command(self, ws, msg):
        cmd = msg.get("command")
        if cmd == "get_state":
            return await ws.send(json.dumps(self.playback_state()))
        if cmd == "get_queue":
            return await ws.send(json.dumps(self.queue()))
        if cmd in ("play", "pause"):
            self.anchor = (self.position_ms(), time.time())
            self.status = "playing" if cmd == "play" else "paused"
            await self.broadcast({"type": "playback_changed", "status": self.status},
                                 {"type": "position_sync", "position": self.position()})
        elif cmd in ("skip_next", "skip_prev"):
            await self.skip(1 if cmd == "skip_next" else -1)
        elif cmd == "seek":
            self.anchor = (int(msg.get("position_ms", 0)), time.time())
            await self.broadcast({"type": "position_sync", "position": self.position()})
        elif cmd == "set_volume":
            self.volume = int(msg.get("volume", 0))
            await self.broadcast({"type": "volume_changed", "volume": self.volume})
        elif cmd in ("activate", "deactivate"):
            self.active = cmd == "activate"
            await self.broadcast({"type": "device_changed", "is_active": self.active,
                                  "device_name": "Living Room TV"})
        else:
            return await ws.send(json.dumps({"type": "error", "message": f"unknown command {cmd!r}"}))
        await ws.send(json.dumps({"type": "command_result", "command": cmd}))

    async def handler(self, ws, *_):
        self.clients.add(ws)
        try:
            await ws.send(json.dumps({"type": "auth_state", "logged_in": True, "is_active": self.active,
                                      "device_name": "Living Room TV"}))
            await ws.send(json.dumps(self.playback_state()))
            async for raw in ws:
                await self.command(ws, json.loads(raw))
        finally:
            self.clients.discard(ws)

    async def auto_advance(self):
        while True:
            await asyncio.sleep(0.5)
            if self.status == "playing":
                dur = self.tracks[self.index]["decorations"]["playback"]["duration_ms"]
                if self.position_ms() >= dur:
                    await self.skip(1)


async def serve(port):
    fake = Fake()
    async with websockets.serve(fake.handler, "127.0.0.1", port):
        print(f"Fake Soloist on ws://127.0.0.1:{port}", flush=True)
        await fake.auto_advance()


async def send(port, cmd, arg):
    msg = {"type": "command", "command": cmd}
    if arg is not None:
        msg[{"seek": "position_ms", "set_volume": "volume"}[cmd]] = int(arg)
    async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
        await ws.send(json.dumps(msg))
        async for raw in ws:
            reply = json.loads(raw)
            if reply["type"] in ("command_result", "error"):
                print(raw)
                return


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=9090)
    p.add_argument("action", nargs="*")
    a = p.parse_args()
    if a.action and a.action[0] == "send":
        asyncio.run(send(a.port, a.action[1], a.action[2] if len(a.action) > 2 else None))
    else:
        asyncio.run(serve(a.port))
