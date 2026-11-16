"""Download album art once per track and prepare everything the page shows.

For each cover this writes, into the cache directory:
  <key>-art.jpg    the cover, 640 px (1280 px at scale 2)
  <key>-thumb.jpg  64 px (128 px) for the Up Next card
  <key>-bg.jpg     480x270, blurred and darkened, stretched full screen by the page
  <key>.json       {"palette": {"base": "#rrggbb"}}
"""

import asyncio
import colorsys
import hashlib
import io
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, ImageEnhance, ImageFilter

log = logging.getLogger("art")

ALLOWED_HOST = "i.scdn.co"
MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 5000 * 5000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # Pillow refuses larger images (decompression bombs)
SIZE_ORDER = ["xlarge", "large", "default", "small"]
# Album cover URLs are /image/ab67616d + 8-hex size code + id. 000082c1 is a
# 2000 px version: undocumented, so it is only tried at scale 2 with a fallback.
ALBUM_COVER = re.compile(r"^(https://i\.scdn\.co/image/ab67616d)[0-9a-f]{8}([0-9a-f]+)$")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a redirect could point anywhere; treat it as a failure


_opener = urllib.request.build_opener(_NoRedirect)


def _fetch(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname != ALLOWED_HOST:
        raise ValueError(f"refusing to fetch {url!r}")
    with _opener.open(url, timeout=10) as resp:
        if not resp.headers.get("Content-Type", "").startswith("image/"):
            raise ValueError(f"not an image: {resp.headers.get('Content-Type')!r}")
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("image too large")
    return data


def _darken_for_white_text(rgb):
    """Darken until white text on it has at least 4.5:1 contrast."""
    def luminance(c):
        c = [v / 255 for v in c]
        c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    while 1.05 / (luminance(rgb) + 0.05) < 4.5:
        rgb = tuple(int(v * 0.9) for v in rgb)
    return rgb


def _palette(img):
    small = img.resize((64, 64)).quantize(8)
    pal = small.getpalette()
    best, best_score = None, -1
    for count, idx in small.getcolors():
        rgb = tuple(pal[idx * 3:idx * 3 + 3])
        _, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
        if v < 0.15 or (v > 0.9 and s < 0.1):
            continue  # near-black or near-white says nothing about the album
        score = count * (0.2 + s)
        if score > best_score:
            best, best_score = rgb, score
    if best is None:
        best = img.resize((1, 1)).getpixel((0, 0))
    return "#%02x%02x%02x" % _darken_for_white_text(best)


def _render(data, cache_dir, key, scale):
    img = Image.open(io.BytesIO(data))
    if img.width * img.height > MAX_PIXELS:
        raise ValueError(f"image too large: {img.size}")
    img.load()
    img = img.convert("RGB")
    size = 640 * scale
    img.resize((size, size), Image.LANCZOS).save(f"{cache_dir}/{key}-art.jpg", quality=88)
    img.resize((64 * scale, 64 * scale), Image.LANCZOS).save(f"{cache_dir}/{key}-thumb.jpg", quality=88)
    # Crop the square cover to 16:9 around the centre, then blur it heavily.
    w, h = img.size
    crop_h = w * 9 // 16
    top = (h - crop_h) // 2
    bg = img.crop((0, top, w, top + crop_h)).resize((480, 270), Image.BILINEAR)
    bg = bg.filter(ImageFilter.GaussianBlur(24))
    ImageEnhance.Brightness(bg).enhance(0.7).save(f"{cache_dir}/{key}-bg.jpg", quality=88)
    with open(f"{cache_dir}/{key}.json", "w") as f:
        json.dump({"palette": {"base": _palette(img)}}, f)


class Art:
    def __init__(self, cache_dir, scale=1, cache_mb=200):
        self.cache_dir = cache_dir
        self.scale = scale
        self.cache_bytes = cache_mb * 1024 * 1024
        self.ready = {}   # key -> result dict
        self.tasks = {}   # key -> asyncio.Task

    def _source(self, cover_list):
        by_size = {c.get("size"): c.get("url") for c in cover_list if c.get("url")}
        best = next((by_size[s] for s in SIZE_ORDER if s in by_size), None)
        if not best:
            return None, None
        big = None
        m = ALBUM_COVER.match(best)
        if self.scale > 1 and m:
            big = f"{m.group(1)}000082c1{m.group(2)}"
        return best, big

    def _key(self, url):
        return hashlib.sha1(f"{url}|{self.scale}".encode()).hexdigest()[:16]

    def _result(self, key):
        try:
            with open(f"{self.cache_dir}/{key}.json") as f:
                palette = json.load(f)["palette"]
        except (OSError, ValueError, KeyError):
            return None
        return {"art": f"/img/{key}-art.jpg", "thumb": f"/img/{key}-thumb.jpg",
                "bg": f"/img/{key}-bg.jpg", "palette": palette}

    def lookup(self, cover_list):
        """The prepared art if it's ready, else None. Never blocks."""
        url, _ = self._source(cover_list)
        return self.ready.get(self._key(url)) if url else None

    def prepare(self, cover_list, on_done=None):
        """Start (or join) preparing art. Returns a task, or None if there's no cover."""
        url, big = self._source(cover_list)
        if not url:
            return None
        key = self._key(url)
        if key in self.ready:
            fut = asyncio.get_running_loop().create_future()
            fut.set_result(self.ready[key])
            return fut
        if key not in self.tasks:
            task = asyncio.create_task(self._prepare(key, url, big))
            self.tasks[key] = task
            task.add_done_callback(lambda _: self.tasks.pop(key, None))
            if on_done:
                task.add_done_callback(lambda _: on_done())
        return self.tasks[key]

    async def _prepare(self, key, url, big):
        result = self._result(key)  # already on disk from an earlier run?
        if not result:
            try:
                await asyncio.to_thread(self._download_and_render, key, url, big)
                result = self._result(key)
            except Exception as e:  # any bad image just means "no art"
                log.warning("Art for %s failed: %s", url, e)
                return None
        self.ready[key] = result
        return result

    def _download_and_render(self, key, url, big):
        data = None
        if big:
            try:
                data = _fetch(big)
            except (OSError, ValueError, urllib.error.URLError) as e:
                log.info("No 2000 px cover, using %s (%s)", url, e)
        _render(data or _fetch(url), self.cache_dir, key, self.scale)
        self._trim()

    def _trim(self):
        files = [e for e in os.scandir(self.cache_dir) if e.is_file()]
        total = sum(e.stat().st_size for e in files)
        for e in sorted(files, key=lambda e: e.stat().st_mtime):
            if total <= self.cache_bytes:
                break
            total -= e.stat().st_size
            os.unlink(e.path)
            key = e.name.split("-")[0].split(".")[0]
            self.ready.pop(key, None)
