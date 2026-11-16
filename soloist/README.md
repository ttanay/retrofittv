# Spotify Soloist on Raspberry Pi

Scripts that run [Spotify Soloist](https://developer.spotify.com/documentation/soloist)
(Spotify's headless Spotify Connect client) as a hardened systemd service, with
its local WebSocket API turned on for control from other code on the Pi.

## Layout

```
install.sh, uninstall.sh   entry points (run with sudo)
bin/                       soloist-set-api-key -> /usr/local/sbin
libexec/                   service internals (launcher, updater, WebSocket proxy and firewall)
                           -> /usr/local/libexec/soloist
systemd/                   unit files -> /etc/systemd/system
config/                    soloist.conf template -> /etc/soloist/soloist.conf
```

## Install

1. Create an API key at <https://developer.spotify.com/dashboard/soloist> (needs Spotify Premium).
2. Copy this directory to the Pi and run:

   ```bash
   sudo ./install.sh --device-name "Retrofit TV"
   ```

   You'll be asked for the API key. Input is hidden.
3. In the Spotify app on the same network, pick **Retrofit TV** from the device list.

Options:
- `--ws-port PORT` changes the WebSocket port (default 9090). The API always listens on
  `127.0.0.1`. `--no-ws` turns it off.
- `--hide-processes` hides every user's processes from other users. This stops them reading
  the API key from Soloist's command line. See the security notes before using it on a
  Pi with a desktop.

Re-running the installer is safe. It keeps your config and key.

## What gets installed

| Path | Purpose |
|---|---|
| `/opt/soloist/soloist` | Soloist binary, downloaded from `soloist-builds.spotifycdn.com`; symlinked as `/usr/local/bin/soloist` |
| `/etc/soloist/soloist.conf` | Non-secret settings: device name, WebSocket, volume, cache ([example](config/soloist.conf.example)) |
| `/etc/soloist/api-key` | API key, `root:root 0600` |
| `/var/lib/soloist`, `/var/cache/soloist` | Device identity, stored Connect session, playback cache (`0700`) |
| `soloist.service` | The daemon, running as the dedicated `soloist` user |
| `soloist-ws-proxy.service` | Origin-checking WebSocket proxy, running as `soloist-proxy` |
| `soloist-update.timer` | Daily update check. Builds **expire after 90 days**. |
| `soloist-set-api-key` | Rotate the key: `sudo soloist-set-api-key` (or pipe it in on stdin) |

## Security model

**API key**
- Stored root-only. The service reads it through systemd `LoadCredential=`. That gives
  the service a private copy, and the service user can't read the original file.
- It is never passed on a command line you type, so it stays out of shell history.
  It isn't stored in `soloist.conf` or the unit file, and the launcher leaves it out of the startup line it logs.
- **Known limit:** Soloist only accepts the key as `--api-key KEY`. While the daemon
  runs, the key is in its argv, and any local user can read it with `ps` or
  `/proc/PID/cmdline`. On a Pi where you are the only user, this doesn't matter much.
  If other people have accounts on the Pi, install with `--hide-processes`. That adds
  `hidepid=invisible` to the `/proc` line in `/etc/fstab` (the original is saved as
  `/etc/fstab.soloist.bak`) and remounts `/proc`. On a headless Pi it's harmless.
  On a desktop it can break polkit password prompts and some system monitors.
  Uninstalling leaves the setting in place.

**WebSocket API**
- Soloist's WebSocket API has *no* authentication, TLS, or Origin checks. It only
  ever listens on `127.0.0.1`, so other machines can't reach it.
- **Browsers are blocked by default.** Any web page open in a browser on the Pi could
  otherwise connect to the port and control playback. Two layers stop that:
  - **Proxy:** a small proxy (`ws-proxy.py`) listens on `SOLOIST_WS_PORT` and refuses
    every handshake that has an `Origin` header. Browsers always send one. `soloist ctl`
    and your own scripts don't.
  - **Firewall:** Soloist itself listens on `SOLOIST_WS_INTERNAL_PORT` (19090). An
    nftables rule (table `inet soloist_ws`) lets only the `soloist-proxy` user connect
    there, so a page can't go around the proxy. The rule is added when Soloist starts
    and removed when it stops. Your own nftables config isn't touched.
- Set `SOLOIST_WS_PROTECT=0` to drop both layers and have Soloist listen on
  `SOLOIST_WS_PORT` directly.
- Programs running on the Pi can still connect and control playback, so don't run
  untrusted software there.
- To control it from another machine, use an SSH tunnel instead of exposing the port:
  `ssh -N -L 9090:127.0.0.1:9090 pi@raspberrypi.local`

**Service sandbox**
- The service runs as a dedicated, non-login `soloist` user with no capabilities.
- `ProtectSystem=strict`, `ProtectHome`, `PrivateDevices`, and `PrivateTmp` restrict
  file and device access, and a syscall filter blocks unusual system calls.
  Audio goes through the user's PipeWire socket.
- Updates download over HTTPS only. Spotify publishes no checksums or signatures for
  these builds, so HTTPS is the only integrity check. The updater refuses archives where
  `soloist` isn't a regular file. It test-runs each new binary as `nobody` before
  installing it, and logs the version and SHA-256 of every build it installs
  (`journalctl -u soloist-update`).

## Using the WebSocket API

Spotify's `soloist ctl` talks to the API. Pass it the endpoint from `soloist.conf`:

```bash
soloist ctl -w 127.0.0.1:9090 status
soloist ctl -w 127.0.0.1:9090 now --json
soloist ctl -w 127.0.0.1:9090 trace     # live event stream
```

Raw protocol: send `{"type":"command","command":"pause"}`. Control commands answer
with `command_result`, and failures come back as `error`. A `command_result` means the
command was *accepted*. Watch the events to see the actual state change.

## Operations

```bash
journalctl -u soloist -f                        # logs
sudo systemctl restart soloist                  # after editing soloist.conf
sudo systemctl start soloist-update             # update now
sudo ./uninstall.sh [--purge]                   # remove (purge deletes key, session, user)
```

## Troubleshooting

- **Device shows up but there's no sound.** The dedicated `soloist` user runs its own
  PipeWire. A desktop session on the Pi may already hold the audio device. Check with
  `sudo systemctl --user -M soloist@ status wireplumber` and
  `sudo -u soloist XDG_RUNTIME_DIR=/run/user/$(id -u soloist) wpctl status`. On a
  headless Pi, nothing else should hold the device. If a desktop does, stop its
  PipeWire (`systemctl --user disable --now pipewire pipewire-pulse wireplumber`
  as that user) or route that desktop's audio to another output.
- **`soloist ctl` gets "403 Forbidden".** The proxy saw an `Origin` header. Check
  `journalctl -u soloist-ws-proxy`. If a trusted local tool sends Origin, set
  `SOLOIST_WS_PROTECT=0` or connect it some other way.
- **Service fails right after starting with a syscall or permission error.** Comment out
  `SystemCallFilter=` or `PrivateDevices=` in `/etc/systemd/system/soloist.service`, run
  `daemon-reload`, and try again. Please report which one it was.
- **Exit code 10.** The build expired. The update service runs automatically when this happens.
