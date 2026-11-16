#!/bin/bash
# Remove the Now Playing TV display.
#   sudo ./uninstall.sh          keep /etc/nowplaying
#   sudo ./uninstall.sh --purge  also delete the config, art cache and users
set -euo pipefail

systemctl disable --now nowplaying-display.service nowplaying-bridge.service 2>/dev/null || true
rm -f /etc/systemd/system/nowplaying-bridge.service /etc/systemd/system/nowplaying-display.service
systemctl daemon-reload
rm -rf /usr/local/libexec/nowplaying /usr/local/share/nowplaying
# Give tty1 back to the login prompt.
systemctl start getty@tty1.service 2>/dev/null || true

if [[ ${1:-} != --purge ]]; then
    echo "Removed the Now Playing display. Kept /etc/nowplaying."
    exit 0
fi

rm -rf /etc/nowplaying /var/cache/nowplaying
if id nowplaying &>/dev/null; then
    userdel nowplaying
fi
if id nowplaying-display &>/dev/null; then
    userdel --remove nowplaying-display
fi
echo "Removed the Now Playing display and all its data."
