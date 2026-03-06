#!/bin/bash
set -u

#############################################################
# Add all containers to be secured in internal/secured.sh   #
#############################################################

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source "${SCRIPT_DIR}/internal/secured.sh"

resolve_wg_ip() {
    # Prefer lsio network IP explicitly; fallback to first attached network IP.
    local ip
    ip="$(docker inspect -f '{{with index .NetworkSettings.Networks "lsio"}}{{.IPAddress}}{{end}}' wireguard 2>/dev/null || true)"
    if [[ -z "$ip" ]]; then
        ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' wireguard 2>/dev/null || true)"
    fi
    echo "$ip"
}

resolve_lsio_gateway() {
    docker network inspect lsio --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true
}

wait_wireguard_ready() {
    local tries=20
    while (( tries > 0 )); do
        if [[ "$(docker inspect -f '{{.State.Running}}' wireguard 2>/dev/null || echo false)" == "true" ]] && \
           docker exec wireguard sh -lc 'ip link show wg0 >/dev/null 2>&1' >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
        ((tries--))
    done
    return 1
}

route_ok() {
    local c="$1" wg_ip="$2"
    docker exec "$c" sh -lc "ip route | grep -q '^default via ${wg_ip} '" >/dev/null 2>&1
}

egress_ok() {
    local c="$1"
    docker exec "$c" sh -lc 'wget -qO- --timeout=8 https://api.github.com >/dev/null 2>&1' >/dev/null 2>&1
}

apply_routes() {
    local c="$1" wg_ip="$2" gw_ip="$3"
    docker exec --privileged "$c" sh -lc "
      ip route del default 2>/dev/null || true
      ip route replace default via ${wg_ip}
      ip route replace 100.64.0.0/10 via ${gw_ip}
    " >/dev/null 2>&1
}

if ! wait_wireguard_ready; then
    echo "ERROR: wireguard is not ready (container down or wg0 missing)." >&2
    exit 2
fi

WG_IP="$(resolve_wg_ip)"
GW_IP="$(resolve_lsio_gateway)"

if [[ -z "$WG_IP" || -z "$GW_IP" ]]; then
    echo "ERROR: could not resolve wireguard/gateway IPs (WG_IP='${WG_IP}', GW_IP='${GW_IP}')." >&2
    exit 3
fi

echo "Using WG_IP=${WG_IP} GW_IP=${GW_IP}"

FAIL=0
for i in "${arr[@]}"; do
    echo -n "(${i}): "

    if [[ "$(docker container inspect -f '{{.State.Running}}' "$i" 2>/dev/null || echo false)" != "true" ]]; then
        echo "container not running!"
        FAIL=1
        continue
    fi

    # If already routed and has egress, keep it.
    if route_ok "$i" "$WG_IP" && egress_ok "$i"; then
        echo "already connected!"
        continue
    fi

    # Retry route apply a few times to avoid startup race conditions.
    OK=0
    for _ in 1 2 3 4 5; do
        apply_routes "$i" "$WG_IP" "$GW_IP"
        sleep 2
        if route_ok "$i" "$WG_IP" && egress_ok "$i"; then
            OK=1
            break
        fi
    done

    if (( OK == 1 )); then
        echo "route restored ✅"
    else
        echo "route restore failed ❌"
        docker exec "$i" ip route 2>/dev/null || true
        FAIL=1
    fi
done

exit $FAIL
