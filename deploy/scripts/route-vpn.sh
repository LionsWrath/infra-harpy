docker exec --privileged qbittorrent ip route del default
docker exec --privileged qbittorrent ip route add default via 172.16.0.50
docker exec --privileged qbittorrent ip route add 172.19.0.0/16 via 172.16.0.1
