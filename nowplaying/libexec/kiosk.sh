#!/bin/bash
# Started by nowplaying-display.service on tty1. Waits for the bridge, then
# shows its page full screen with the browser chosen at install time.
set -euo pipefail

port=${NOWPLAYING_PORT:-9095}
url="http://127.0.0.1:$port/"

# Don't show a browser error page while the bridge is still starting.
until (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; do
    sleep 1
done

case ${NOWPLAYING_ENGINE:-chromium} in
    chromium)
        # cage: a minimal Wayland compositor that runs one app full screen.
        # -s allows Ctrl+Alt+Fn to switch to a console (handy with a keyboard).
        exec cage -s -- chromium --kiosk --ozone-platform=wayland --incognito \
            --noerrdialogs --no-first-run --disable-features=Translate \
            --disable-component-update --mute-audio \
            "$url"
        ;;
    cog)
        # WPE WebKit drawing straight to the display; no compositor needed.
        exec cog --platform=drm "$url"
        ;;
    *)
        echo "Unknown NOWPLAYING_ENGINE '$NOWPLAYING_ENGINE' (use chromium or cog)" >&2
        exit 1
        ;;
esac
