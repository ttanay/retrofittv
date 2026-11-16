"""Tests for bridge/state.py.   Run:  python3 -m unittest discover -s tests"""

import copy
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bridge"))

import state  # noqa: E402

with open(os.path.join(HERE, "fixtures", "playback_state.json")) as f:
    REAL = json.load(f)

NO_ART = lambda item: None  # noqa: E731


def logged_in(**overrides):
    s = state.State()
    s.set_connected(True)
    s.apply({"type": "auth_state", "logged_in": True, "is_active": True, "device_name": "TV"})
    msg = copy.deepcopy(REAL)
    msg.update(overrides)
    s.apply(msg)
    return s


class Describe(unittest.TestCase):
    def test_real_capture(self):
        snap = logged_in(status="playing").snapshot(NO_ART)
        t = snap["track"]
        self.assertEqual(snap["screen"], "playing")
        self.assertEqual(t["title"], "Sunshine Of Your Love")
        self.assertEqual(t["subtitle"], "Cream")
        self.assertEqual(t["collection"], "Disraeli Gears (Deluxe Edition)")
        self.assertEqual(t["duration_ms"], 250400)
        self.assertFalse(t["explicit"])
        self.assertIsNone(snap["context"], "album context repeats the album, so no label")

    def test_playlist_context_gets_a_label(self):
        ctx = {"uri": "spotify:playlist:x", "entity_type": "playlist",
               "decorations": {"identity": {"name": "Classic Rock"}}}
        snap = logged_in(context=ctx).snapshot(NO_ART)
        self.assertEqual(snap["context"], {"label": "PLAYING FROM PLAYLIST", "name": "Classic Rock"})

    def test_missing_decorations_dont_crash(self):
        d = state.describe({"uri": "spotify:track:x", "entity_type": "track"})
        self.assertEqual((d["title"], d["subtitle"], d["collection"]), ("", "", ""))

    def test_episode_falls_back_to_show_name(self):
        ep = {"entity_type": "episode", "decorations": {
            "identity": {"name": "Ep 1"},
            "parent": {"entity": {"decorations": {"identity": {"name": "The Show"}}}}}}
        d = state.describe(ep)
        self.assertEqual((d["subtitle"], d["collection"]), ("The Show", "The Show"))

    def test_ad(self):
        self.assertEqual(state.describe({"entity_type": "ad"})["title"], "Advertisement")

    def test_covers_fall_back_to_parent(self):
        item = {"decorations": {"parent": {"entity": {"decorations": {
            "visual_identity": {"cover": [{"url": "u", "size": "large"}]}}}}}}
        self.assertEqual(state.covers(item), [{"url": "u", "size": "large"}])


class Screens(unittest.TestCase):
    def test_paused_then_idle_after_timeout(self):
        s = logged_in(status="paused")
        self.assertEqual(s.screen(), "paused")
        with mock.patch("time.monotonic", return_value=s.paused_since + 601):
            self.assertEqual(s.screen(), "idle")

    def test_another_device_takes_over(self):
        s = logged_in(status="playing")
        s.apply({"type": "device_changed", "is_active": False, "device_name": "TV"})
        self.assertEqual(s.screen(), "idle")

    def test_logged_out_is_off(self):
        s = logged_in(status="playing")
        s.apply({"type": "auth_state", "logged_in": False, "is_active": False})
        self.assertEqual(s.screen(), "off")

    def test_disconnect_keeps_screen_briefly(self):
        s = logged_in(status="playing")
        s.snapshot(NO_ART)
        s.set_connected(False)
        self.assertEqual(s.screen(), "playing")
        with mock.patch("time.monotonic", return_value=s.down_since + 11):
            self.assertEqual(s.screen(), "off")


class UpNext(unittest.TestCase):
    def queue(self, s, item):
        s.apply({"type": "queue_changed", "upcoming": [{"uid": "x", "item": item}]})

    def test_next_track_shown(self):
        s = logged_in(status="playing")
        nxt = {"uri": "spotify:track:next", "entity_type": "track",
               "decorations": {"identity": {"name": "Strange Brew"}}}
        self.queue(s, nxt)
        self.assertEqual(s.snapshot(NO_ART)["up_next"]["title"], "Strange Brew")

    def test_hidden_on_repeat_track_or_same_track(self):
        s = logged_in(status="playing")
        self.queue(s, REAL["item"])
        self.assertIsNone(s.snapshot(NO_ART)["up_next"])
        self.queue(s, {"uri": "spotify:track:next", "entity_type": "track"})
        s.apply({"type": "options_changed", "options": {"repeat": "track"}})
        self.assertIsNone(s.snapshot(NO_ART)["up_next"])


if __name__ == "__main__":
    unittest.main()
