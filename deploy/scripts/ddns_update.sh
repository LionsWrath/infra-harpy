#!/bin/bash

curl -X POST -s https://${DDNS_USER}:${DDNS_PWD}@domains.google.com/nic/update?hostname=${DDNS_DOMAIN}&myip=$(curl -s ifconfig.me)
