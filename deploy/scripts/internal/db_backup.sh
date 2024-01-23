#!/bin/bash

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source ${SCRIPT_DIR}/ntfy.sh

if [ "$( docker container inspect -f '{{.State.Running}}' postgres )" = "true" ]; then
    
    FILENAME=$(date +"%Y_%m_%d")_pg_bck.gz
    FILEPATH=${DATA_PATH}/backups/postgresql

    docker exec postgres pg_dumpall -U harpydb | gzip > ${FILEPATH}/${FILENAME}

    if [ ! -f ${FILEPATH}/${FILENAME} ]; then
        echo "File ${FILENAME} not found! Backup failed."
    else
        echo "Backup successful. File ${FILENAME} created."
    fi

else
    echo "Container postgres not running!"
fi