#!/bin/bash
# Started by soloist.service. Reads the API key from the service's systemd
# credential and the settings from /etc/soloist/soloist.conf, then runs Soloist.
set -euo pipefail

key=$(<"$CREDENTIALS_DIRECTORY/api-key")

args=(
    --device-name "$SOLOIST_DEVICE_NAME"
    --data-dir "$STATE_DIRECTORY"
    --cache-dir "$CACHE_DIRECTORY"
)

# The WebSocket API has no authentication, so it only ever listens on
# loopback. With protection on, Soloist listens on an internal port that only
# the Origin-checking proxy (soloist-ws-proxy.service) is allowed to reach,
# and the proxy listens on SOLOIST_WS_PORT.
if [[ -n ${SOLOIST_WS_PORT:-} ]]; then
    port=$SOLOIST_WS_PORT
    if [[ ${SOLOIST_WS_PROTECT:-1} == 1 ]]; then
        port=${SOLOIST_WS_INTERNAL_PORT:-19090}
    fi
    if [[ ! $port =~ ^[0-9]+$ ]]; then
        echo "WebSocket port must be a number, got '$port'" >&2
        exit 1
    fi
    args+=(--ws "127.0.0.1:$port")
fi

[[ -n ${SOLOIST_INITIAL_VOLUME:-} ]]  && args+=(--initial-volume "$SOLOIST_INITIAL_VOLUME")
[[ -n ${SOLOIST_CACHE_SIZE_MB:-} ]]   && args+=(--cache-size "$SOLOIST_CACHE_SIZE_MB")
[[ -n ${SOLOIST_PIPEWIRE_DEVICE:-} ]] && args+=(--pipewire-device "$SOLOIST_PIPEWIRE_DEVICE")
[[ ${SOLOIST_VERBOSE:-0} == 1 ]]      && args+=(--verbose)

echo "Starting soloist ${args[*]}"
exec /opt/soloist/soloist "${args[@]}" --api-key "$key"
