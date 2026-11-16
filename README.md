# Retrofit TV

**Smart-TV features for the TV you already have.**

Retrofit TV turns a Raspberry Pi into a companion for an older TV. It adds the modern
features the TV is missing, without replacing the TV. Each feature is a self-contained
component you can install, update and remove on its own.

> **Status:** early. The first component, Spotify playback, works and is tested on a
> Raspberry Pi. More components are planned.

## Features

- 🎵 **Spotify Connect.** The TV shows up as a speaker in every Spotify app. Pick it from
  your phone and the music plays through the TV, powered by
  [Spotify Soloist](https://developer.spotify.com/documentation/soloist).
- 📺 **Now Playing screen.** The TV shows the album art, song, artist and album
  of whatever's playing, Spotify-style, and a clock when nothing is.
- 🔌 **Local control API.** Other software on the Pi can control playback over a
  WebSocket API.
- 🔄 **Hands-off.** It starts at boot without anyone logging in, and updates itself.
- 🔒 **Secure by default.** Each service runs sandboxed as its own user, secrets are
  stored root-only, and local APIs can't be reached from the network or from web pages.

## Quick start

You'll need:
- a Raspberry Pi running Raspberry Pi OS (tested on Debian Trixie, 64-bit);
- an audio output: HDMI to the TV, or a USB audio device;
- a Spotify Premium account and a
  [Soloist API key](https://developer.spotify.com/dashboard/soloist).

From your computer, copy the Spotify component to the Pi and log in:

```bash
git clone <this-repo-url> retrofittv && cd retrofittv
git archive HEAD soloist | ssh pi@raspberrypi.local 'tar -x -C ~'
ssh pi@raspberrypi.local
```

Then, on the Pi:

```bash
cd ~/soloist
sudo ./install.sh --device-name "Living Room TV"
```

Paste your API key when asked, then pick **Living Room TV** in the Spotify app.

## Components

| Component | Status | Description |
|---|---|---|
| [`soloist/`](soloist/README.md) | ✅ Working | Spotify Connect speaker with a local WebSocket API |
| [`nowplaying/`](nowplaying/README.md) | 🧪 Built, not yet tested on a Pi | Album art and track info on the TV ([design](nowplaying/DESIGN.md)) |

Each component has its own `install.sh`, `uninstall.sh` and README. Re-running an
installer is safe: it keeps your settings and secrets.

## How it works

```
 Spotify app ──(Spotify Connect)──▶ Raspberry Pi ──(HDMI / USB audio)──▶ TV
                                        │
                                        ├─ soloist.service             Spotify playback
                                        ├─ soloist-ws-proxy.service    local control API
                                        ├─ nowplaying-bridge.service   track info + album art
                                        └─ nowplaying-display.service  full-screen page on HDMI
```

Every component runs as a systemd service under its own system user, with a sandbox
that limits what it can access. See the
[Soloist security model](soloist/README.md#security-model) for details.

## Project layout

```
retrofittv/
├── README.md        you are here
├── soloist/         Spotify Connect component
└── nowplaying/      Now Playing screen component
    ├── install.sh   installer (run with sudo on the Pi)
    ├── uninstall.sh
    ├── bin/         admin commands
    ├── libexec/     service scripts
    ├── systemd/     unit files
    └── config/      settings template
```

## Troubleshooting

Start with the logs, then check the component's README:

```bash
systemctl status soloist
journalctl -u soloist -f
```

Common issues and fixes are listed in the
[Soloist troubleshooting guide](soloist/README.md#troubleshooting).
