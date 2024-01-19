docker exec --privileged qbittorrent ip route del default
docker exec --privileged qbittorrent ip route add default via 172.19.0.50
docker exec --privileged qbittorrent ip route add 100.64.0.0/10 via 172.19.0.1