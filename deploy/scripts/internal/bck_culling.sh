#!/bin/bash

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source ${SCRIPT_DIR}/../vars.sh
source ${SCRIPT_DIR}/ntfy.sh

BACKUP_PATH=${DATA_PATH}/backups/postgresql
RETENTION_TIME=30

arr1=$(ls -t ${BACKUP_PATH} | head -n $RETENTION_TIME)
arr2=$(ls -t ${BACKUP_PATH})

if (( ${#arr2[@]} -gt ${#RETENTION_TIME} )); then
    notify "Backup Culling Process" "default" "heavy_check_mark" "No file to CULL." ${MANAGE_TOPIC}
else
    line=$(echo ${arr1[@]} ${arr2[@]} | tr ' ' '\n' | sort | uniq -u)
    readarray -t res <<< "$line"

    for i in "${res[@]}"
    do
        rm ${BACKUP_PATH}/$i
    done

    notify "Backup Culling Process" "high" "warning" "Culling ${res[@]} file(s)." ${MANAGE_TOPIC}   

fi
