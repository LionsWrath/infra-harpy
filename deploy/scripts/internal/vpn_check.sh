#!/bin/bash

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source ${SCRIPT_DIR}/telegram.sh
source ${SCRIPT_DIR}/secured.sh

TITLE="VPN Verification Process"

#---------------------------------------------------------------------------------------------
# Is wireguard on?
# If not we stop all secured containers for safety

if [ "$( docker container inspect -f '{{.State.Running}}' wireguard )" = "true" ]; then
    if [ "$( check_container_vpn wireguard )" = "false" ]; then
        notify "$TITLE" "high" "warning" "Base wg container not connected! Emergency stop." "infra"
        stop_all_secured_containers       
        exit
    fi
else
    notify "$TITLE" "high" "danger" "Base wg container not running! Emergency stop." "infra"
    stop_all_secured_containers
    exit
fi

#---------------------------------------------------------------------------------------------
# Check other containers + try route recovery before stop

restore_vpn_route () {
    local c="$1"
    local wg_ip gw_ip

    wg_ip="$(docker inspect -f '{{with index .NetworkSettings.Networks "lsio"}}{{.IPAddress}}{{end}}' wireguard 2>/dev/null || true)"
    [[ -z "$wg_ip" ]] && wg_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' wireguard 2>/dev/null || true)"
    gw_ip="$(docker network inspect lsio --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true)"

    if [[ -z "$wg_ip" || -z "$gw_ip" ]]; then
        return 1
    fi

    docker exec --privileged "$c" sh -lc "
      ip route del default 2>/dev/null || true
      ip route replace default via ${wg_ip}
      ip route replace 100.64.0.0/10 via ${gw_ip}
    " >/dev/null 2>&1 || true

    sleep 4
}

RECOVERED=()
STOPPED=()
MISSING=()

for i in "${arr[@]}"
do
    if [ "$( docker container inspect -f '{{.State.Running}}' $i 2>/dev/null )" = "true" ]; then
        if [ "$( check_container_vpn $i )" = "false" ]; then
            restore_vpn_route "$i"

            if [ "$( check_container_vpn $i )" = "true" ]; then
                RECOVERED+=("$i")
            else
                STOPPED+=("$i")
                docker stop "$i" >/dev/null 2>&1 || true
            fi
        fi
    else
        MISSING+=("$i")
    fi
done

if (( ${#MISSING[@]} > 0 )); then
    MISS_LIST=$( IFS=$', '; echo "${MISSING[*]}" )
    notify "$TITLE" "high" "danger" "Container(s) not running: ${MISS_LIST}" "infra"
fi

if (( ${#RECOVERED[@]} > 0 )); then
    REC_LIST=$( IFS=$', '; echo "${RECOVERED[*]}" )
    notify "$TITLE" "default" "ok" "Container(s) route restored and connected: ${REC_LIST}" "infra"
fi

if (( ${#STOPPED[@]} > 0 )); then
    STOP_LIST=$( IFS=$', '; echo "${STOPPED[*]}" )
    notify "$TITLE" "high" "warning" "Container(s) still not connected after route recovery: ${STOP_LIST}. Stopped." "infra"
fi

if (( ${#MISSING[@]} == 0 && ${#STOPPED[@]} == 0 && ${#RECOVERED[@]} == 0 )); then
    notify "$TITLE" "default" "ok" "All containers connected!" "infra"
fi