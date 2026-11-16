#!/bin/bash
# Download the latest Soloist build and install it as /opt/soloist/soloist.
# soloist-update.timer runs this daily because Soloist builds expire 90 days
# after they're built. Spotify publishes no checksums, so we rely on HTTPS.
set -euo pipefail

# dpkg reports the userland architecture. uname -m would say aarch64 on a
# 32-bit Raspberry Pi OS running a 64-bit kernel.
case $(dpkg --print-architecture) in
    arm64) arch=arm64 ;;
    armhf) arch=arm32 ;;
    amd64) arch=x86_64 ;;
    *) echo "Soloist doesn't support $(dpkg --print-architecture)" >&2; exit 1 ;;
esac

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

curl -fsSL --proto '=https' --retry 3 -o "$tmp/soloist.tar.gz" \
    "https://soloist-builds.spotifycdn.com/soloist_release_$arch.tar.gz"
tar -xzf "$tmp/soloist.tar.gz" -C "$tmp" --no-same-owner

# A symlink here would make the root-run chmod and install below act on
# whatever file it points to.
if [[ ! -f $tmp/soloist || -L $tmp/soloist ]]; then
    echo "The download doesn't contain a regular 'soloist' file" >&2
    exit 1
fi

# Make sure the new build runs before installing it, without trusting it with root.
chmod 755 "$tmp" "$tmp/soloist"
version=$(runuser -u nobody -- "$tmp/soloist" --version)

if cmp -s "$tmp/soloist" /opt/soloist/soloist; then
    echo "Soloist $version is already up to date"
    exit 0
fi

# Install next to the old binary and rename, so the swap is atomic.
install -D -m 755 "$tmp/soloist" /opt/soloist/soloist.new
mv /opt/soloist/soloist.new /opt/soloist/soloist

# Spotify doesn't sign its builds, so log what we installed for later checks.
echo "Installed Soloist $version"
echo "sha256: $(sha256sum < /opt/soloist/soloist | cut -d' ' -f1)"

# Restart on the new build. This also revives a service that failed
# because its build expired.
if systemctl is-enabled -q soloist.service 2>/dev/null; then
    systemctl reset-failed soloist.service
    systemctl restart soloist.service
fi
