"""Turn Soloist WebSocket events into the snapshot the TV page shows.

Pure logic, no I/O: feed events to State.apply(), read State.snapshot().
"""

import time

OFF_GRACE_S = 10  # keep the last screen this long while Soloist is unreachable


def _name(entity):
    return ((entity or {}).get("decorations") or {}).get("identity", {}).get("name", "")


def _deco(entity):
    return (entity or {}).get("decorations") or {}


def covers(item):
    """Cover images for an item, falling back to its parent (album or show)."""
    d = _deco(item)
    found = (d.get("visual_identity") or {}).get("cover") or []
    if not found:
        parent = (d.get("parent") or {}).get("entity")
        found = (_deco(parent).get("visual_identity") or {}).get("cover") or []
    return found


def describe(item):
    """Title, subtitle and collection for a track, episode or ad."""
    d = _deco(item)
    kind = (item or {}).get("entity_type") or "unknown"
    if kind == "ad":
        return {"key": item.get("uri", ""), "kind": "ad", "title": "Advertisement",
                "subtitle": "", "collection": "", "explicit": False, "duration_ms": 0}
    parent = (d.get("parent") or {}).get("entity")
    artists = [_name(c.get("entity")) for c in d.get("creators") or []]
    subtitle = ", ".join(a for a in artists if a)
    if kind == "episode" and not subtitle:
        subtitle = _name(parent)
    playback = d.get("playback") or {}
    return {
        "key": item.get("uri", ""),
        "kind": kind,
        "title": _name(item),
        "subtitle": subtitle,
        "collection": _name(parent),
        "explicit": "explicit" in (playback.get("content_ratings") or []),
        "duration_ms": playback.get("duration_ms") or 0,
    }


def context_label(context, item):
    """The 'PLAYING FROM …' line, or None when it would add nothing."""
    name = _name(context)
    if not context or not name:
        return None
    kind = context.get("entity_type")
    album = (_deco(item).get("parent") or {}).get("entity") or {}
    if kind == "album" and context.get("uri") == album.get("uri"):
        return None
    label = {"playlist": "PLAYING FROM PLAYLIST", "artist": "PLAYING FROM ARTIST",
             "album": "PLAYING FROM ALBUM"}.get(kind, "PLAYING FROM")
    return {"label": label, "name": name}


class State:
    def __init__(self, pause_to_idle_s=600):
        self.pause_to_idle_s = pause_to_idle_s
        self.connected = False
        self.synced = False  # got a playback_state since connecting
        self.down_since = time.monotonic() - OFF_GRACE_S  # start as "off"
        self.last_screen = "off"
        self.logged_in = False
        self.is_active = False
        self.device_name = ""
        self.status = "idle"
        self.paused_since = None
        self.item = None
        self.context = None
        self.position = None
        self.repeat = "off"
        self.upcoming = None
        self.last_played = None

    def set_connected(self, up):
        if self.connected and not up:
            self.down_since = time.monotonic()
        self.connected = up
        self.synced = False

    def _set_status(self, status):
        if status == "paused" and self.status != "paused":
            self.paused_since = time.monotonic()
        self.status = status

    def _set_item(self, item):
        self.item = item or None
        if self.item and self.item.get("entity_type") != "ad" and _name(self.item):
            d = describe(self.item)
            self.last_played = {"title": d["title"], "subtitle": d["subtitle"]}

    def apply(self, msg):
        t = msg.get("type")
        if t == "auth_state":
            self.logged_in = bool(msg.get("logged_in"))
            self.is_active = bool(msg.get("is_active"))
            self.device_name = msg.get("device_name") or self.device_name
        elif t == "playback_state":
            self._set_status(msg.get("status") or "idle")
            self._set_item(msg.get("item"))
            self.context = msg.get("context")
            self.position = msg.get("position")
            self.is_active = bool(msg.get("is_active"))
            self.repeat = (msg.get("options") or {}).get("repeat", "off")
            self.logged_in = True  # get_state only answers when logged in
            self.synced = True
        elif t == "track_changed":
            self._set_item(msg.get("item"))
        elif t == "playback_changed":
            self._set_status(msg.get("status") or "idle")
        elif t == "position_sync":
            self.position = msg.get("position")
        elif t == "device_changed":
            self.is_active = bool(msg.get("is_active"))
            self.device_name = msg.get("device_name") or self.device_name
        elif t == "context_changed":
            self.context = msg.get("context")
        elif t == "options_changed":
            self.repeat = (msg.get("options") or {}).get("repeat", "off")
        elif t == "queue_changed":
            upcoming = msg.get("upcoming") or []
            self.upcoming = upcoming[0].get("item") if upcoming else None

    def screen(self):
        now = time.monotonic()
        if not self.connected:
            if now - self.down_since > OFF_GRACE_S:
                return "off"
            return self.last_screen
        if not self.logged_in:
            return "off"
        if not self.synced:
            return self.last_screen  # auth_state comes just before the snapshot
        if not self.is_active or not self.item or self.status not in ("playing", "paused", "buffering"):
            return "idle"
        if self.status == "paused" and now - (self.paused_since or now) > self.pause_to_idle_s:
            return "idle"
        return self.status

    def up_next(self):
        nxt = self.upcoming
        if not nxt or self.repeat == "track" or nxt.get("uri") == (self.item or {}).get("uri"):
            return None
        if nxt.get("entity_type") == "ad":
            return None
        return nxt

    def snapshot(self, art_for):
        """The SSE payload. art_for(item) returns {"art","thumb","bg","palette"} or None."""
        self.last_screen = screen = self.screen()
        track = None
        if self.item:
            track = describe(self.item)
            art = art_for(self.item) if track["kind"] != "ad" else None
            track.update(art or {"art": None, "thumb": None, "bg": None, "palette": None})
            track.pop("thumb")
        up_next = None
        nxt = self.up_next()
        if nxt:
            d = describe(nxt)
            art = art_for(nxt) or {}
            up_next = {"title": d["title"], "subtitle": d["subtitle"], "thumb": art.get("thumb")}
        return {
            "screen": screen,
            "device_name": self.device_name,
            "track": track,
            "context": context_label(self.context, self.item) if self.item else None,
            "position": self.position,
            "up_next": up_next,
            "last_played": self.last_played,
        }
