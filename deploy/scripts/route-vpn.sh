#############################################################
# Add all containers to be secured here                     #
#############################################################
# Route configuration                                       #
#-----------------------------------------------------------#
# WG      = 172.19.0.50                                     #
# GATEWAY = 172.19.0.1                                      #
# TAILNET = 100.64.0.0/10                                   #
#############################################################

declare -a arr=(
    "qbittorrent"
    "sonarr"
    "radarr"
    "bazarr"
    "jackett"
)

for i in "${arr[@]}"
do
    echo -n "($i): "

    docker exec --privileged $i ip route del default
    docker exec --privileged $i ip route add default via 172.19.0.50
    docker exec --privileged $i ip route add 100.64.0.0/10 via 172.19.0.1

    docker exec $i curl -s https://am.i.mullvad.net/connected
done