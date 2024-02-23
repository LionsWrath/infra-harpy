#!/bin/bash

check_container_vpn () {
    CHECK=$(docker exec $1 curl -s https://am.i.mullvad.net/connected)

    if [[ $CHECK == *"You are not connected to Mullvad."* ]]; then
        echo "false"
    else
        echo "true"
    fi
}

stop_all_secured_containers () {
    for i in "${arr[@]}"
    do
        docker stop $i
    done
}

###########################################################################
# flaresolverr doesn't need to be added because it uses the same stack as
# the jackett docker
###########################################################################
declare -a arr=(
    "qbittorrent"
    "sonarr"
    "radarr"
    "bazarr"
    "jackett"
)