#!/bin/bash
# Install the Now Playing TV display on a Raspberry Pi. Needs soloist/ installed.
#
#   sudo ./install.sh [--engine chromium|cog] [--4k] [--no-cec]
#
#   --engine   Browser for the display. Default: chromium with 1.5 GB RAM or
#              more, otherwise cog (WPE WebKit, lighter).
#   --4k       Fetch sharper album art for a 4K TV.
#   --no-cec   Don't turn the TV on over HDMI-CEC.
#
# Safe to re-run: an existing /etc/nowplaying/nowplaying.conf is kept, and the
# options above only apply to a new config.
set -euo pipefail
cd "$(dirname "$0")"

engine=
scale=1
cec=1
while [[ $# -gt 0 ]]; do
    case $1 in
        --engine) engine=$2; shift 2 ;;
        --4k)     scale=2; shift ;;
        --no-cec) cec=0; shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -z $engine ]]; then
    mem_kb=$(awk '/^MemTotal/ {print $2}' /proc/meminfo)
    engine=$([[ $mem_kb -ge 1500000 ]] && echo chromium || echo cog)
fi
if [[ $engine != chromium && $engine != cog ]]; then
    echo "--engine must be chromium or cog" >&2
    exit 1
fi

ws_port=$( (. /etc/soloist/soloist.conf 2>/dev/null && echo "${SOLOIST_WS_PORT:-}") || true)
if [[ -z $ws_port ]]; then
    echo "Soloist isn't installed or its WebSocket API is off. Install soloist/ first." >&2
    exit 1
fi

# An existing config decides the engine, so install the right browser for it.
if [[ -f /etc/nowplaying/nowplaying.conf ]]; then
    engine=$(. /etc/nowplaying/nowplaying.conf && echo "${NOWPLAYING_ENGINE:-chromium}")
fi

echo "==> Installing packages ($engine)"
packages=(python3 python3-websockets python3-pil v4l-utils kbd)  # kbd: chvt
if [[ $engine == chromium ]]; then
    packages+=(cage chromium)
else
    packages+=(cog)
fi
apt-get update -q
apt-get install -y -q --no-install-recommends "${packages[@]}"

echo "==> Setting up users"
if ! id nowplaying &>/dev/null; then
    useradd --system --user-group --no-create-home --shell /usr/sbin/nologin nowplaying
fi
if ! id nowplaying-display &>/dev/null; then
    # The browser keeps its profile in the home directory.
    useradd --system --user-group --create-home --home-dir /var/lib/nowplaying-display \
        --shell /usr/sbin/nologin nowplaying-display
fi
usermod -aG video,render nowplaying-display

# The display's login session would start its own PipeWire, which can grab
# the HDMI audio device from Soloist. Mask it for this user only.
units_dir=/var/lib/nowplaying-display/.config/systemd/user
mkdir -p "$units_dir"
for unit in pipewire.service pipewire.socket pipewire-pulse.service pipewire-pulse.socket \
            wireplumber.service; do
    ln -sfn /dev/null "$units_dir/$unit"
done
chown -R nowplaying-display:nowplaying-display /var/lib/nowplaying-display/.config

echo "==> Installing files"
rm -rf /usr/local/libexec/nowplaying /usr/local/share/nowplaying
install -d /usr/local/libexec/nowplaying/bridge /usr/local/share/nowplaying/www/fonts
install -m 644 bridge/*.py /usr/local/libexec/nowplaying/bridge/
install -m 755 libexec/kiosk.sh /usr/local/libexec/nowplaying/kiosk
install -m 644 www/index.html www/app.css www/app.js /usr/local/share/nowplaying/www/
install -m 644 www/fonts/* /usr/local/share/nowplaying/www/fonts/

if [[ ! -f /etc/nowplaying/nowplaying.conf ]]; then
    mkdir -p /etc/nowplaying
    sed -e "s|^NOWPLAYING_ENGINE=.*|NOWPLAYING_ENGINE=$engine|" \
        -e "s|^NOWPLAYING_SCALE=.*|NOWPLAYING_SCALE=$scale|" \
        -e "s|^NOWPLAYING_CEC=.*|NOWPLAYING_CEC=$cec|" \
        config/nowplaying.conf.example > /etc/nowplaying/nowplaying.conf
fi

echo "==> Installing systemd units"
cp systemd/nowplaying-bridge.service systemd/nowplaying-display.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable nowplaying-bridge.service nowplaying-display.service
systemctl restart nowplaying-bridge.service
systemctl restart nowplaying-display.service

cat <<EOF

Done. The TV shows the Now Playing screen on the Pi's HDMI output.

  Status:    systemctl status nowplaying-bridge nowplaying-display
  Logs:      journalctl -u nowplaying-bridge -u nowplaying-display -f
  Settings:  /etc/nowplaying/nowplaying.conf
             (then: sudo systemctl restart nowplaying-bridge nowplaying-display)
EOF
