#############################################################
# Add all containers to be secured here                     #
#############################################################
# Route configuration                                       #
#-----------------------------------------------------------#
# WG      = 172.19.0.50                                     #
# GATEWAY = 172.19.0.1                                      #
# TAILNET = 100.64.0.0/10                                   #
#############################################################

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source ${SCRIPT_DIR}/internal/secured.sh

for i in "${arr[@]}"
do
    echo -n "($i): "

    if [ "$( docker container inspect -f '{{.State.Running}}' $i )" = "true" ]; then

        if [ "$( check_container_vpn $i )" = "true" ]; then
            echo "already connected!"
            continue
        fi

        docker exec --privileged $i ip route del default
        docker exec --privileged $i ip route add default via 172.19.0.50
        docker exec --privileged $i ip route add 100.64.0.0/10 via 172.19.0.1

        docker exec $i curl -s https://am.i.mullvad.net/connected
    else
        echo "container not running!"
    fi
done