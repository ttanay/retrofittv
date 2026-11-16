"""Turn the TV on (and optionally off) over HDMI-CEC, using cec-ctl."""

import asyncio
import logging
import os
import re
import shutil

log = logging.getLogger("cec")
DEVICE = "/dev/cec0"


class Cec:
    def __init__(self, osd_name):
        self.osd_name = osd_name[:14] or "Retrofit TV"  # CEC allows 14 characters
        self.phys_addr = None
        self.enabled = os.path.exists(DEVICE) and shutil.which("cec-ctl") is not None
        if not self.enabled:
            log.info("No %s or cec-ctl; TV power control is off", DEVICE)

    async def _run(self, *args):
        proc = await asyncio.create_subprocess_exec(
            "cec-ctl", "-d", DEVICE, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        out = out.decode(errors="replace")
        if proc.returncode != 0:
            log.warning("cec-ctl %s failed: %s", " ".join(args), out.strip()[-200:])
        return out

    async def setup(self):
        if not self.enabled:
            return
        out = await self._run("--playback", "--osd-name", self.osd_name)
        m = re.search(r"Physical Address\s*:\s*([0-9a-f]\.[0-9a-f]\.[0-9a-f]\.[0-9a-f])", out)
        if m and m.group(1) != "f.f.f.f":
            self.phys_addr = m.group(1)
            log.info("CEC ready at physical address %s", self.phys_addr)
        else:
            log.warning("CEC: no physical address (TV off or not connected?)")

    async def wake(self):
        """Turn the TV on and switch it to the Pi's input."""
        if not self.enabled:
            return
        if not self.phys_addr:
            await self.setup()
        log.info("Waking the TV")
        await self._run("--to", "0", "--image-view-on")
        if self.phys_addr:
            await self._run("--to", "15", "--active-source", f"phys-addr={self.phys_addr}")

    async def standby(self):
        if self.enabled:
            log.info("Putting the TV in standby")
            await self._run("--to", "0", "--standby")
