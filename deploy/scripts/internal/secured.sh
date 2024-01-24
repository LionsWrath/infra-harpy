#!/bin/bash

check_container_vpn () {
    CHECK=$(docker exec $1 curl -s https://am.i.mullvad.net/connected)

    if [[ $CHECK == *"You are not connected to Mullvad."* ]]; then
        echo "false"
    else
        echo "true"
    fi
}

declare -a arr=(
    "qbittorrent"
    "sonarr"
    "radarr"
    "bazarr"
    "jackett"
)