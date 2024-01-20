ip route del default
ip route add default via 172.19.0.50
ip route add 100.64.0.0/10 via 172.19.0.1