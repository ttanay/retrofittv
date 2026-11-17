#!/bin/bash
# Install Spotify Soloist as a systemd service on a Raspberry Pi.
#
#   sudo ./install.sh [--device-name NAME] [--ws-port PORT | --no-ws] [--hide-processes]
#
#   --device-name NAME  Name shown in Spotify's device list (default: "Retrofit TV").
#                       Letters, digits, spaces and . _ ' ( ) - only.
#   --ws-port PORT      WebSocket API port on 127.0.0.1 (default: 9090).
#   --no-ws             Turn the WebSocket API off.
#   --hide-processes    Mount /proc with hidepid=invisible so other users can't
#                       read the API key from Soloist's command line. Can break
#                       polkit prompts on a desktop; see the README.
#
# Safe to re-run: an existing config and API key are kept.
set -euo pipefail
cd "$(dirname "$0")"

device_name="Retrofit TV"
ws_port=9090
hide_processes=0

while [[ $# -gt 0 ]]; do
    case $1 in
        --device-name)    device_name=$2; shift 2 ;;
        --ws-port)        ws_port=$2; shift 2 ;;
        --no-ws)          ws_port=; shift ;;
        --hide-processes) hide_processes=1; shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# These values end up in the config file, so keep them to safe characters.
if [[ ! $device_name =~ ^[A-Za-z0-9\ ._\'\(\)-]+$ ]]; then
    echo "Device name may only contain letters, digits, spaces and . _ ' ( ) -" >&2
    exit 1
fi
if [[ ! $ws_port =~ ^[0-9]*$ ]]; then
    echo "WebSocket port must be a number" >&2
    exit 1
fi

echo "==> Installing packages"
apt-get update -q
apt-get install -y -q curl nftables python3 pipewire pipewire-pulse wireplumber

echo "==> Setting up users"
if ! id soloist &>/dev/null; then
    # PipeWire keeps state in the home directory, so this user needs one.
    useradd --system --user-group --create-home --home-dir /var/lib/soloist-home \
        --shell /usr/sbin/nologin soloist
fi
chmod 700 /var/lib/soloist-home
usermod -aG audio soloist
uid=$(id -u soloist)

# The WebSocket proxy runs as its own user, the only one the firewall rule
# lets reach Soloist's internal port.
if ! id soloist-proxy &>/dev/null; then
    useradd --system --user-group --no-create-home --shell /usr/sbin/nologin soloist-proxy
fi

# Linger starts the user's session (and PipeWire with it) at boot, without
# anyone logging in.
loginctl enable-linger soloist
systemctl start "user@$uid.service"
systemctl --user -M soloist@ enable --now pipewire.socket pipewire-pulse.socket wireplumber.service

if [[ $hide_processes == 1 ]]; then
    echo "==> Hiding other users' processes"
    if ! grep -qE '^[^#]*[[:space:]]/proc[[:space:]].*hidepid' /etc/fstab; then
        cp /etc/fstab /etc/fstab.soloist.bak
        if grep -qE '^[^#[:space:]]+[[:space:]]+/proc[[:space:]]' /etc/fstab; then
            # Add hidepid to the options of the existing /proc line.
            sed -Ei 's|^([^#[:space:]]+[[:space:]]+/proc[[:space:]]+proc[[:space:]]+)([^[:space:]]+)|\1\2,hidepid=invisible|' /etc/fstab
        else
            echo "proc /proc proc defaults,hidepid=invisible 0 0" >> /etc/fstab
        fi
    fi
    mount -o remount,hidepid=invisible /proc
fi

echo "==> Installing scripts"
install -D -m 755 libexec/launch.sh /usr/local/libexec/soloist/launch
install -D -m 755 libexec/update.sh /usr/local/libexec/soloist/update
install -D -m 755 libexec/ws-firewall /usr/local/libexec/soloist/ws-firewall
install -D -m 755 libexec/output-volume /usr/local/libexec/soloist/output-volume
install -D -m 644 libexec/ws-proxy.py /usr/local/libexec/soloist/ws-proxy.py
install -D -m 755 bin/soloist-set-api-key /usr/local/sbin/soloist-set-api-key

if [[ ! -f /etc/soloist/soloist.conf ]]; then
    mkdir -p /etc/soloist
    sed -e "s|^SOLOIST_DEVICE_NAME=.*|SOLOIST_DEVICE_NAME=\"$device_name\"|" \
        -e "s|^SOLOIST_WS_PORT=.*|SOLOIST_WS_PORT=$ws_port|" \
        config/soloist.conf.example > /etc/soloist/soloist.conf
fi

if [[ ! -s /etc/soloist/api-key ]]; then
    echo "Paste your API key from https://developer.spotify.com/dashboard/soloist"
    /usr/local/sbin/soloist-set-api-key
fi

echo "==> Downloading Soloist"
/usr/local/libexec/soloist/update
ln -sf /opt/soloist/soloist /usr/local/bin/soloist

echo "==> Installing systemd units"
sed "s|@UID@|$uid|g" systemd/soloist.service.in > /etc/systemd/system/soloist.service
cp systemd/soloist-ws-proxy.service systemd/soloist-update.service systemd/soloist-update.timer \
    /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now soloist-update.timer
systemctl enable soloist.service
systemctl restart soloist.service

# An existing config wins over --ws-port, so show the port it actually uses.
ws_port=$(. /etc/soloist/soloist.conf && echo "$SOLOIST_WS_PORT")

cat <<EOF

Done. Pick your device in the Spotify app to start playing.

  Status:    systemctl status soloist soloist-ws-proxy
  Logs:      journalctl -u soloist -u soloist-ws-proxy -f
  Control:   soloist ctl -w 127.0.0.1:${ws_port:-9090} status
  New key:   sudo soloist-set-api-key
  Settings:  /etc/soloist/soloist.conf (then: sudo systemctl restart soloist)
EOF
