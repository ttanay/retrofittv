"""nowplaying-bridge: watch Soloist and serve a read-only "now playing" feed to the TV page.

Run as:  python3 -I /usr/local/libexec/nowplaying/bridge
Settings come from the environment (see nowplaying.conf.example).
"""

import asyncio
import json
import logging
import os
import time

import websockets

from art import Art
from cec import Cec
from server import Hub, Server
from state import State, covers

log = logging.getLogger("bridge")
ART_WAIT_S = 1.5   # how long a track change waits for its art before showing anyway
TICK_S = 5         # re-check time-based screen changes (pause -> idle, standby)


def env_int(name, default):
    value = os.environ.get(name, "").strip()
    return int(value) if value else default


class Bridge:
    def __init__(self):
        self.soloist_url = f"ws://127.0.0.1:{env_int('SOLOIST_WS_PORT', 9090)}"
        self.state = State(pause_to_idle_s=60 * env_int("NOWPLAYING_PAUSE_TO_IDLE", 10))
        # systemd sets CACHE_DIRECTORY; when run by hand, use ~/.cache/nowplaying.
        cache_dir = os.environ.get("CACHE_DIRECTORY") or os.path.expanduser("~/.cache/nowplaying")
        os.makedirs(cache_dir, exist_ok=True)
        self.art = Art(cache_dir,
                       scale=env_int("NOWPLAYING_SCALE", 1),
                       cache_mb=env_int("NOWPLAYING_CACHE_MB", 200))
        self.hub = Hub()
        self.cec = Cec(os.environ.get("SOLOIST_DEVICE_NAME", "Retrofit TV")) \
            if env_int("NOWPLAYING_CEC", 1) else None
        self.standby_s = 60 * env_int("NOWPLAYING_IDLE_TO_STANDBY", 0)
        self.screen = "off"
        self.idle_since = time.monotonic()
        self.standby_sent = False
        www = os.environ.get("NOWPLAYING_WWW") or os.path.join(os.path.dirname(__file__), "..", "www")
        self.server = Server(self.hub, env_int("NOWPLAYING_PORT", 9095), www, self.art.cache_dir,
                             lambda: {"soloist": "connected" if self.state.connected else "down"})

    def publish(self):
        snap = self.state.snapshot(lambda item: self.art.lookup(covers(item)))
        self.hub.publish(snap)
        self.on_screen(snap["screen"])

    def on_screen(self, screen):
        before, self.screen = self.screen, screen
        if screen == before:
            return
        log.info("Screen: %s -> %s", before, screen)
        if screen == "idle":
            self.idle_since = time.monotonic()
        if screen == "playing" and before in ("off", "idle") and self.cec:
            self.standby_sent = False
            asyncio.create_task(self.cec.wake())

    async def tick(self):
        while True:
            await asyncio.sleep(TICK_S)
            self.publish()
            if (self.cec and self.standby_s and self.screen == "idle" and not self.standby_sent
                    and time.monotonic() - self.idle_since > self.standby_s):
                self.standby_sent = True
                asyncio.create_task(self.cec.standby())

    async def handle(self, ws, msg):
        t = msg.get("type")
        if t == "error":
            log.warning("Soloist error: %s", msg.get("message"))
            return
        if t == "volume_changed":
            self.hub.event("volume", {"volume": msg.get("volume")})
            return
        if t in ("track_changed", "playback_state") and msg.get("item"):
            # Hold the update briefly so the page crossfades once, with the art.
            task = self.art.prepare(covers(msg["item"]), on_done=self.publish)
            if task:
                try:
                    await asyncio.wait_for(asyncio.shield(task), ART_WAIT_S)
                except asyncio.TimeoutError:
                    log.info("Art is slow; showing the track without it for now")
        self.state.apply(msg)
        if t == "auth_state" and msg.get("logged_in"):
            for cmd in ({"command": "get_state"}, {"command": "get_queue", "limit": 3}):
                await ws.send(json.dumps({"type": "command", **cmd}))
        if t == "queue_changed" and self.state.up_next():
            self.art.prepare(covers(self.state.up_next()), on_done=self.publish)  # prefetch
        self.publish()

    async def soloist(self):
        delay = 1
        while True:
            try:
                # No pings: this is loopback, so a dead Soloist closes the socket.
                async with websockets.connect(self.soloist_url, ping_interval=None,
                                              max_size=4 * 1024 * 1024) as ws:
                    log.info("Connected to Soloist at %s", self.soloist_url)
                    delay = 1
                    self.state.set_connected(True)
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except ValueError:
                            continue
                        if isinstance(msg, dict):
                            await self.handle(ws, msg)
            except (OSError, websockets.exceptions.WebSocketException) as e:
                log.warning("Soloist connection: %s", e or type(e).__name__)
            self.state.set_connected(False)
            self.publish()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def run(self):
        server = await self.server.start()
        log.info("Serving the TV page on http://127.0.0.1:%d/", self.server.port)
        if self.cec:
            await self.cec.setup()
        self.publish()
        async with server:
            await asyncio.gather(self.soloist(), self.tick())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    asyncio.run(Bridge().run())
