#!/bin/bash

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source ${SCRIPT_DIR}/ntfy.sh
source ${SCRIPT_DIR}/secured.sh

TITLE="VPN Verification Process"

if [ "$( docker container inspect -f '{{.State.Running}}' wireguard )" = "true" ]; then
    if [ "$( check_container_vpn wireguard )" = "false" ]; then
        notify "$TITLE" "high" "warning" "Base wg container not connected!" ${MANAGE_TOPIC}
        exit
    fi
else
    notify "$TITLE" "urgent" "rotating_light" "Base wg container not running!" ${MANAGE_TOPIC}
    exit
fi

#---------------------------------------------------------------------------------------------
# Check other containers

COT=()

for i in "${arr[@]}"
do
    if [ "$( docker container inspect -f '{{.State.Running}}' $i )" = "true" ]; then
        if [ "$( check_container_vpn $i )" = "false" ]; then
            COT+=("$i")
        fi
    else
        notify "$TITLE" "urgent" "rotating_light" "Container $i not running!" ${MANAGE_TOPIC}
        COT+=("$i")
    fi
done

if (( ${#COT[@]} == 0 )); then
    notify "$TITLE" "default" "heavy_check_mark" "All containers connected!" ${MANAGE_TOPIC}
else
    CONTAINERS=$( IFS=$', '; echo "${COT[*]}" )   
    notify "$TITLE" "high" "warning" "Container(s) ${CONTAINERS} not connected!" ${MANAGE_TOPIC}
fi