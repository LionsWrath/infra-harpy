#!/bin/bash

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source ${SCRIPT_DIR}/ntfy.sh

if [ "$( docker container inspect -f '{{.State.Running}}' postgres )" = "true" ]; then
    
    FILENAME=$(date +"%Y_%m_%d")_pg_bck.gz
    FILEPATH=${DATA_PATH}/backups/postgresql

    if [ ! -f ${FILEPATH}/${FILENAME} ]; then
        notify "DB Backup Process" "high" "warning" "File ${FILENAME} already exists. Exiting." ${MANAGE_TOPIC}
        exit
    fi

    docker exec postgres pg_dumpall -U harpydb | gzip > ${FILEPATH}/${FILENAME}

    if [ ! -f ${FILEPATH}/${FILENAME} ]; then
        notify "DB Backup Process" "high" "warning" "File ${FILENAME} not found! Backup failed." ${MANAGE_TOPIC}
    else
        notify "DB Backup Process" "default" "heavy_check_mark" "Backup successful. File ${FILENAME} created." ${MANAGE_TOPIC}
    fi

else
    notify "DB Backup Process" "urgent" "rotating_light" "Container postgres not running!" ${MANAGE_TOPIC}
fi