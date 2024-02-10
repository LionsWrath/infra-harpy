#!/bin/bash

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source ${SCRIPT_DIR}/../vars.sh

BACKUP_PATH=${DATA_PATH}/backups/postgresql
RETENTION_TIME=30

arr1=$(ls -t ${BACKUP_PATH} | head -n $RETENTION_TIME)
arr2=$(ls -t ${BACKUP_PATH})

if (( ${#arr1[@]} == 0)); then
    echo "No file to cull."
else
    line=$(echo ${arr1[@]} ${arr2[@]} | tr ' ' '\n' | sort | uniq -u)
    readarray -t res <<< "$line"

    for i in "${res[@]}"
    do
        echo ${BACKUP_PATH}/$i
    done

fi
