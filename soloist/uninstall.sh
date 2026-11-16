#!/bin/bash
# Remove the Soloist service.
#   sudo ./uninstall.sh          keep the config, API key and Spotify session
#   sudo ./uninstall.sh --purge  delete those and the soloist users too
set -euo pipefail

systemctl stop soloist-ws-proxy.service 2>/dev/null || true
systemctl disable --now soloist.service soloist-update.timer 2>/dev/null || true
rm -f /etc/systemd/system/soloist.service /etc/systemd/system/soloist-update.* \
      /etc/systemd/system/soloist-ws-proxy.service
systemctl daemon-reload
nft delete table inet soloist_ws 2>/dev/null || true

rm -rf /opt/soloist /usr/local/libexec/soloist
rm -f /usr/local/sbin/soloist-set-api-key
# Only remove the soloist command if it's our symlink.
if [[ $(readlink /usr/local/bin/soloist) == /opt/soloist/soloist ]]; then
    rm /usr/local/bin/soloist
fi

if [[ -f /etc/fstab.soloist.bak ]]; then
    echo "Note: /proc is still mounted with hidepid=invisible. Your original fstab is in /etc/fstab.soloist.bak."
fi

if [[ ${1:-} != --purge ]]; then
    echo "Removed Soloist. Kept /etc/soloist and /var/lib/soloist."
    exit 0
fi

# Overwrite the key before deleting it. SD cards remap blocks, so this is
# best effort; revoke the key in the Spotify dashboard to be sure.
if [[ -f /etc/soloist/api-key ]]; then
    shred -u /etc/soloist/api-key
fi
rm -rf /etc/soloist /var/lib/soloist /var/cache/soloist
if id soloist &>/dev/null; then
    loginctl disable-linger soloist
    userdel --remove soloist
fi
if id soloist-proxy &>/dev/null; then
    userdel soloist-proxy
fi
echo "Removed Soloist and all its data. You can revoke the API key in the Spotify dashboard."
