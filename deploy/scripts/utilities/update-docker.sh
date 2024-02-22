#!/bin/bash

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)

docker stop $1
docker rm $1

docker-compose -f ${SCRIPT_DIR}/../../apps/docker-compose.$2.yml up -d
